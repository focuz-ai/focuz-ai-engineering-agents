from dataclasses import replace
import unittest
from agents.core import Principal
from agents.issue_tests import Issue, read_issue, propose_cases, render_markdown


class IssueToTestsTests(unittest.TestCase):
    def setUp(self):
        self.principal = Principal('A', 'qa', ('qa',))
        self.issue = Issue('A', 'focuz-ai/demo', 17, 'Ticket autorizado', 'approved_ticket')

    def test_five_distinct_cases_are_traceable_and_not_execution_results(self):
        cases = propose_cases(self.issue, self.principal)
        self.assertEqual(len(cases), 5)
        self.assertEqual(len({case['test_id'] for case in cases}), 5)
        self.assertEqual({case['scenario'] for case in cases},
                         {'happy', 'missing_approval', 'foreign_tenant', 'replay', 'revoked_role'})
        self.assertTrue(all(case['issue_id'] == 17 and case['preconditions'] and case['input'] and case['expected'] for case in cases))
        text = render_markdown(self.issue, cases)
        self.assertIn('no demuestra que las pruebas se hayan ejecutado', text)
        for case in cases:
            self.assertIn(case['test_id'], text)

    def test_read_scope_and_role_enforced_before_proposal(self):
        self.assertEqual(read_issue([self.issue], self.principal, 'focuz-ai/demo', 17, ('focuz-ai/demo',)), self.issue)
        for principal, allowed in ((Principal('B', 'qa', ('qa',)), ('focuz-ai/demo',)),
                                   (Principal('A', 'qa', ()), ('focuz-ai/demo',)), (self.principal, ())):
            with self.assertRaises(PermissionError):
                read_issue([self.issue], principal, 'focuz-ai/demo', 17, allowed)
        with self.assertRaises(PermissionError):
            propose_cases(self.issue, Principal('B', 'qa', ('qa',)))

    def test_unknown_requirement_abstains_instead_of_fabricating_cases(self):
        with self.assertRaises(ValueError):
            propose_cases(replace(self.issue, spec_kind='arbitrary_feature'), self.principal)

    def test_untrusted_markdown_is_escaped_and_wrong_issue_rejected(self):
        issue = replace(self.issue, title='[ejecutar](https://example.invalid)\n<script>x</script>|`x`')
        cases = propose_cases(issue, self.principal)
        text = render_markdown(issue, cases)
        self.assertNotIn('<script>', text)
        self.assertNotIn('\n<script>', text)
        self.assertNotIn('[ejecutar]', text)
        self.assertIn('&lt;script&gt;', text)
        with self.assertRaises(ValueError):
            render_markdown(issue, [dict(cases[0], issue_id=18)])
