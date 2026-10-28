from pathlib import Path
from tempfile import TemporaryDirectory
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
    def test_payload_cannot_change_on_retry(self):
        with self.assertRaises(ValueError):
            self.w.propose('r1',self.author,'create_local_ticket','Otro resumen')
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
