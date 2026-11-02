from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import unittest
from agents.core import Workflow, Principal

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.w = Workflow()
        self.author = Principal('A','ana',('support',))
        self.approver = Principal('A','luisa',('approver',))
        self.executor = Principal('A','service',('executor',))
        self.p = self.w.propose('r1',self.author,'create_local_ticket','Resumen ficticio')
    def tearDown(self): self.w.close()
    def test_requires_approval(self):
        with self.assertRaises(PermissionError): self.w.execute('r1',self.executor)
        self.assertEqual(self.w.ticket_count(),0)
    def test_exact_local_idempotency(self):
        self.w.approve('r1',self.approver,self.p['digest'])
        a = self.w.execute('r1',self.executor)
        self.assertEqual(a,self.w.execute('r1',self.executor))
        self.assertEqual(self.w.ticket_count(),1)
    def test_tenant_cannot_approve(self):
        with self.assertRaises(PermissionError):
            self.w.approve('r1',Principal('B','bob',('approver',)),self.p['digest'])
    def test_tenant_cannot_execute(self):
        self.w.approve('r1',self.approver,self.p['digest'])
        with self.assertRaises(PermissionError):
            self.w.execute('r1',Principal('B','service',('executor',)))
    def test_no_self_approval(self):
        with self.assertRaises(PermissionError):
            self.w.approve('r1',Principal('A','ana',('approver',)),self.p['digest'])
    def test_digest_bound(self):
        with self.assertRaises(ValueError): self.w.approve('r1',self.approver,'different')
        self.assertFalse(self.w.db.in_transaction)
        self.w.approve('r1',self.approver,self.p['digest'])
    def test_approval_locks_before_read_and_cannot_revive_done(self):
        # Intercalar dos conexiones de forma determinista, sin sleeps ni carreras
        # de scheduling: el ejecutor intenta escribir justo tras la lectura.
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            path = str(Path(d)/'approval-race.sqlite')
            approver = Workflow(path)
            executor = Workflow(path)
            try:
                proposal = approver.propose('race',self.author,
                                           'create_local_ticket','Concurrente')
                approver.approve('race',self.approver,proposal['digest'])
                executor.db.execute('PRAGMA busy_timeout=0')
                original_row = approver._row
                locked_reads = []
                def interleaved_read(request_id, principal):
                    row = original_row(request_id, principal)
                    locked_reads.append(approver.db.in_transaction)
                    with self.assertRaises(sqlite3.OperationalError) as caught:
                        executor.execute('race',self.executor)
                    self.assertIn('locked',str(caught.exception).lower())
                    return row
                approver._row = interleaved_read
                approver.approve('race',self.approver,proposal['digest'])
                approver._row = original_row
                self.assertEqual(locked_reads,[True])
                result = executor.execute('race',self.executor)
                with self.assertRaises(ValueError):
                    approver.approve('race',self.approver,proposal['digest'])
                state = approver.db.execute(
                    'SELECT state FROM requests WHERE id=?',('race',)).fetchone()[0]
                self.assertEqual(state,'DONE')
                self.assertEqual(result,executor.execute('race',self.executor))
                self.assertEqual(executor.ticket_count(),1)
            finally:
                approver.close()
                executor.close()
    def test_payload_cannot_change_on_retry(self):
        with self.assertRaises(ValueError):
            self.w.propose('r1',self.author,'create_local_ticket','Otro resumen')
        self.assertFalse(self.w.db.in_transaction)
    def _concurrent_proposals(self, second_author, second_summary):
        # Both connections are initialized before work starts. Events put the
        # second call at the first call's read/write boundary, without sleeps.
        # For a broken implementation with an unlocked SELECT, force both reads
        # to finish before either INSERT; a locked SELECT releases the owner as
        # soon as the other connection attempts to begin its transaction.
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            path = str(Path(d)/'proposal-race.sqlite')
            initial = Workflow(path)
            initial.close()
            ready = threading.Barrier(2)
            first_read = threading.Event()
            second_attempt = threading.Event()
            second_read = threading.Event()
            def worker(index, principal, summary):
                workflow = Workflow(path)
                connection = workflow.db
                class ObservedCursor:
                    def __init__(self, cursor): self.cursor = cursor
                    def fetchone(self):
                        row = self.cursor.fetchone()
                        self.cursor.close()
                        if index == 0:
                            first_read.set()
                            if not second_attempt.wait(10):
                                raise AssertionError('Second proposal did not start')
                            if not connection.in_transaction and not second_read.wait(10):
                                raise AssertionError('Second unlocked read did not finish')
                        else:
                            second_read.set()
                        return row
                class ObservedConnection:
                    def __getattr__(self, name): return getattr(connection,name)
                    def __enter__(self):
                        connection.__enter__()
                        return self
                    def __exit__(self, *args): return connection.__exit__(*args)
                    def execute(self, sql, parameters=()):
                        selecting = sql.startswith('SELECT tenant,author,digest,state')
                        if index == 1 and (sql == 'BEGIN IMMEDIATE' or selecting):
                            second_attempt.set()
                        cursor = connection.execute(sql,parameters)
                        return ObservedCursor(cursor) if selecting else cursor
                workflow.db = ObservedConnection()
                try:
                    ready.wait(timeout=10)
                    if index == 1 and not first_read.wait(10):
                        raise AssertionError('First proposal did not read')
                    try:
                        value = workflow.propose('race',principal,'create_local_ticket',summary)
                        return {'value':value,'transaction_open':connection.in_transaction}
                    except Exception as exc:
                        return {'error':exc,'transaction_open':connection.in_transaction}
                finally:
                    workflow.close()
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(worker,0,self.author,'Concurrente')
                second = pool.submit(worker,1,second_author,second_summary)
                outcomes = [first.result(timeout=20),second.result(timeout=20)]
            check = Workflow(path)
            try:
                rows = check.db.execute('SELECT tenant,author,payload FROM requests').fetchall()
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0][:2],(self.author.tenant,self.author.user))
                self.assertIn('Concurrente',rows[0][2])
                self.assertEqual(check.ticket_count(),0)
            finally:
                check.close()
            return outcomes
    def test_concurrent_identical_proposals_return_same_result(self):
        first, second = self._concurrent_proposals(self.author,'Concurrente')
        self.assertNotIn('error',first)
        self.assertNotIn('error',second)
        self.assertEqual(first['value'],second['value'])
        self.assertEqual(first['value']['state'],'PENDING')
        self.assertFalse(first['transaction_open'])
        self.assertFalse(second['transaction_open'])
    def test_concurrent_proposal_conflicts_preserve_scope_author_and_payload(self):
        for author, summary in [
            (Principal('B','ana',('support',)),'Concurrente'),
            (Principal('A','bob',('support',)),'Concurrente'),
            (self.author,'Contenido cambiado'),
        ]:
            with self.subTest(tenant=author.tenant,user=author.user,summary=summary):
                first, second = self._concurrent_proposals(author,summary)
                self.assertNotIn('error',first)
                self.assertIsInstance(second.get('error'),ValueError)
                self.assertIn('ámbito o payload',str(second['error']))
                self.assertFalse(second['transaction_open'])
    def test_allowlist(self):
        with self.assertRaises(ValueError):
            self.w.propose('r2',self.author,'run_shell','malicioso')
    def test_role_enforced(self):
        with self.assertRaises(PermissionError):
            self.w.propose('r2',Principal('A','x',()),'create_local_ticket','hola')
        with self.assertRaises(PermissionError): self.w.approve('r1',self.author,self.p['digest'])
    def test_sql_parameterization(self):
        self.w.propose('r2',self.author,'create_local_ticket',"x'); DROP TABLE tickets;--")
        self.assertEqual(self.w.ticket_count(),0)
    def test_resume_from_disk(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            path = str(Path(d)/'state.sqlite')
            w = Workflow(path)
            proposal = w.propose('p',self.author,'create_local_ticket','Persistente')
            w.approve('p',self.approver,proposal['digest']); w.close()
            w = Workflow(path)
            result = w.execute('p',self.executor)
            w.close()
            w = Workflow(path)
            self.assertEqual(result,w.execute('p',self.executor))
            self.assertEqual(w.ticket_count(),1)
            w.close()

if __name__ == '__main__': unittest.main()
