from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import json
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from agents.core import Principal, Workflow
from agents.github_tools import GitHubToolDemo
from agents.estimation import EstimationFlow, read_workshop_exchange


class GitHubToolsTests(unittest.TestCase):
    def setUp(self):
        self.flow = Workflow()
        self.addCleanup(self.flow.close)
        self.tool = GitHubToolDemo(self.flow, ('focuz-ai/demo',))
        self.author = Principal('A', 'ana', ('support',))
        self.reviewer = Principal('A', 'bea', ('approver',))
        self.executor = Principal('A', 'worker', ('executor',))
        self.args = {'repository': 'focuz-ai/demo', 'title': 'Regresión', 'body': 'Datos ficticios'}

    def test_approval_bound_to_payload_and_single_local_effect(self):
        proposal = self.tool.propose('gh1', self.author, 'create_issue', self.args)
        with self.assertRaises(PermissionError):
            self.tool.execute('gh1', self.executor)
        self.assertEqual(self.flow.ticket_count(), 0)
        with self.assertRaises(ValueError):
            self.tool.propose('gh1', self.author, 'create_issue', dict(self.args, title='Cambió'))
        self.flow.approve('gh1', self.reviewer, proposal['digest'])
        first = self.tool.execute('gh1', self.executor)
        self.assertEqual(first, self.tool.execute('gh1', self.executor))
        self.assertFalse(first['remote_issue_created'])
        self.assertEqual(self.flow.ticket_count(), 1)

    def test_model_cannot_supply_permissions_or_change_tool_or_target(self):
        for name, arguments, error in (
                ('approve', self.args, ValueError),
                ('create_issue', dict(self.args, roles=['approver']), ValueError),
                ('create_issue', dict(self.args, repository='other/private'), PermissionError),
                ('create_issue', dict(self.args, title=' '), ValueError)):
            with self.subTest(name=name, arguments=arguments), self.assertRaises(error):
                self.tool.propose('gh1', self.author, name, arguments)
        self.assertEqual(self.flow.ticket_count(), 0)

    def test_tenant_and_revoked_execution_denied_on_replay(self):
        proposal = self.tool.propose('gh1', self.author, 'create_issue', self.args)
        self.flow.approve('gh1', self.reviewer, proposal['digest'])
        self.tool.execute('gh1', self.executor)
        for user in (Principal('B', 'worker', ('executor',)), Principal('A', 'worker', ())):
            with self.assertRaises(PermissionError):
                self.tool.execute('gh1', user)
        with self.assertRaises(PermissionError):
            GitHubToolDemo(self.flow, ()).execute('gh1', self.executor)


class EstimationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'flow.sqlite3'
        self.flow = EstimationFlow(self.path)
        self.addCleanup(lambda: self.flow.close())
        self.author = Principal('A', 'ana', ('support',))
        self.reviewer = Principal('A', 'bea', ('approver',))
        self.estimator = Principal('A', 'worker', ('estimator',))
        self.tasks = [{'id': 'a', 'description': 'Login usuarios'}, {'id': 'b', 'description': 'Nueva tarea'}]
        self.estimates = [{'id': 'a', 'hours': '10', 'evidence': ['historical-1']},
                          {'id': 'b', 'hours': None, 'evidence': []}]
        self.proposal = self.flow.propose('est1', self.author, self.tasks, '60', '10')

    def first_gate(self):
        self.flow.approve_breakdown('est1', self.reviewer, self.proposal['plan_digest'])

    def candidate(self, estimates=None):
        return self.flow.submit_estimates('est1', self.estimator, self.proposal['plan_digest'],
                                         self.estimates if estimates is None else estimates)

    def test_gates_missing_evidence_human_override_restart_and_idempotency(self):
        with self.assertRaises(PermissionError):
            self.candidate()
        self.first_gate()
        candidate = self.candidate()
        with self.assertRaises(ValueError):
            self.flow.approve_final('est1', self.reviewer, candidate['estimate_digest'])
        self.assertFalse(self.flow.db.in_transaction)
        self.flow.close()
        self.flow = EstimationFlow(self.path)
        result = self.flow.approve_final('est1', self.reviewer, candidate['estimate_digest'], {'b': '4'})
        self.assertEqual((result['state'], result['hours'], result['amount']), ('DONE', '14', '924.00'))
        self.assertFalse(result['commercial_proposal_sent'])
        self.assertEqual(result['lines'][1]['hours_origin'], 'human')
        self.assertEqual(result, self.flow.approve_final('est1', self.reviewer, candidate['estimate_digest'], {'b': '4'}))
        with self.assertRaises(ValueError):
            self.flow.approve_final('est1', self.reviewer, candidate['estimate_digest'], {'b': '5'})
        with self.assertRaises(PermissionError):
            self.candidate()

    def test_changed_estimate_invalidates_old_second_gate(self):
        self.first_gate()
        old = self.candidate()
        changed = [dict(self.estimates[0], hours='12'), self.estimates[1]]
        new = self.candidate(changed)
        self.assertNotEqual(new['estimate_digest'], old['estimate_digest'])
        with self.assertRaises(ValueError):
            self.flow.approve_final('est1', self.reviewer, old['estimate_digest'], {'b': '4'})
        self.assertEqual(self.flow.approve_final('est1', self.reviewer, new['estimate_digest'], {'b': '4'})['amount'], '1056.00')

    def test_roles_tenants_and_separation_of_duties(self):
        for actor in (Principal('B', 'bea', ('approver',)), Principal('A', 'ana', ('approver',)),
                      Principal('A', 'bea', ())):
            with self.assertRaises(PermissionError):
                self.flow.approve_breakdown('est1', actor, self.proposal['plan_digest'])
        self.first_gate()
        candidate = self.candidate()
        for actor in (Principal('B', 'bea', ('approver',)), Principal('A', 'ana', ('approver',)),
                      Principal('A', 'worker', ('approver',)), Principal('A', 'bea', ())):
            with self.assertRaises(PermissionError):
                self.flow.approve_final('est1', actor, candidate['estimate_digest'], {'b': '4'})

    def test_digest_scope_and_task_set_are_immutable(self):
        with self.assertRaises(ValueError):
            self.flow.approve_breakdown('est1', self.reviewer, 'different')
        with self.assertRaises(ValueError):
            self.flow.propose('est1', Principal('B', 'ana', ('support',)), self.tasks, '60', '10')
        with self.assertRaises(ValueError):
            self.flow.propose('est1', self.author, self.tasks, '61', '10')
        self.first_gate()
        for estimates in ([self.estimates[0]], [self.estimates[0], self.estimates[0]],
                          [dict(self.estimates[0], evidence=[]), self.estimates[1]],
                          [dict(self.estimates[0], hours='NaN'), self.estimates[1]],
                          [dict(self.estimates[0], id='unexpected'), self.estimates[1]]):
            with self.subTest(estimates=estimates), self.assertRaises(ValueError):
                self.candidate(estimates)
        candidate = self.candidate()
        with self.assertRaises(ValueError):
            self.flow.approve_final('est1', self.reviewer, candidate['estimate_digest'], {'unknown': '4'})

    def test_invalid_business_numbers_and_duplicate_tasks_fail_before_storage(self):
        for rate, contingency in (('NaN', '0'), ('60', 'Infinity'), ('0', '0'), ('60', '101'), (.1, '0')):
            with self.subTest(rate=rate, contingency=contingency), self.assertRaises(ValueError):
                self.flow.propose('invalid', self.author, self.tasks, rate, contingency)
        with self.assertRaises(ValueError):
            self.flow.propose('duplicate', self.author, [self.tasks[0], self.tasks[0]], '60')
        self.assertEqual(self.flow.db.execute('SELECT COUNT(*) FROM estimation_flows').fetchone()[0], 1)

    def test_concurrent_final_approvals_have_one_persisted_result(self):
        self.first_gate()
        candidate = self.candidate()
        barrier = Barrier(4)
        def approve(_):
            connection = EstimationFlow(self.path)
            try:
                barrier.wait(timeout=10)
                return connection.approve_final('est1', self.reviewer, candidate['estimate_digest'], {'b': '4'})
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(approve, range(4)))
        self.assertTrue(all(value == results[0] for value in results))
        self.assertEqual(self.flow.db.execute("SELECT COUNT(*) FROM estimation_flows WHERE state='DONE'").fetchone()[0], 1)

    def test_storage_failure_rolls_back_final_transition_and_allows_retry(self):
        self.first_gate()
        candidate = self.candidate()
        self.flow.db.execute("CREATE TRIGGER stop_done BEFORE UPDATE ON estimation_flows WHEN NEW.state='DONE' BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.flow.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.flow.approve_final('est1', self.reviewer, candidate['estimate_digest'], {'b': '4'})
        row = self.flow.db.execute('SELECT state,result,final_decision FROM estimation_flows').fetchone()
        self.assertEqual(tuple(row), ('FINAL_PENDING', None, None))
        self.assertFalse(self.flow.db.in_transaction)
        self.flow.db.execute('DROP TRIGGER stop_done')
        self.flow.db.commit()
        self.assertEqual(self.flow.approve_final('est1', self.reviewer, candidate['estimate_digest'], {'b': '4'})['amount'], '924.00')

    def test_exchange_cannot_set_identity_or_unbounded_payload(self):
        path = Path(self.temp.name) / 'exchange.json'
        fixture = {'schema_version': 1, 'tenant': 'A', 'tasks': self.tasks, 'estimates': self.estimates,
                   'data_kind': 'synthetic_workshop_fixture'}
        path.write_text(json.dumps(fixture), encoding='utf-8')
        self.assertEqual(read_workshop_exchange(path, 'A'), fixture)
        with self.assertRaises(PermissionError):
            read_workshop_exchange(path, 'B')
        for value in (dict(fixture, roles=['approver']), dict(fixture, schema_version=True),
                      dict(fixture, schema_version=2)):
            path.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError):
                read_workshop_exchange(path, 'A')
        for raw in (b'not json', b'\xff\xff', b' ' * 102401):
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                read_workshop_exchange(path, 'A')
