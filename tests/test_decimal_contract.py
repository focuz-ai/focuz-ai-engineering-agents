from decimal import ROUND_DOWN, localcontext
from fractions import Fraction
import unittest
from agents.core import Principal
from agents.estimation import EstimationFlow


def exact_cents(hours, rate, contingency):
    value = sum(Fraction(h) for h in hours) * Fraction(rate) * (1 + Fraction(contingency) / 100) * 100
    cents = (2 * value.numerator + value.denominator) // (2 * value.denominator)
    return f'{cents // 100}.{cents % 100:02d}'


class DecimalContractTests(unittest.TestCase):
    author = Principal('A', 'author', ('support',))
    estimator = Principal('A', 'estimator', ('estimator',))
    reviewer = Principal('A', 'reviewer', ('approver',))

    def complete(self, hours, rate, contingency='0'):
        flow = EstimationFlow()
        try:
            proposal = flow.propose('money', self.author,
                                    [{'id': str(i), 'description': 'Fixture'} for i in range(len(hours))], rate, contingency)
            flow.approve_breakdown('money', self.reviewer, proposal['plan_digest'])
            estimate = flow.submit_estimates('money', self.estimator, proposal['plan_digest'],
                                             [{'id': str(i), 'hours': h, 'evidence': ['fixture']} for i, h in enumerate(hours)])
            return flow.approve_final('money', self.reviewer, estimate['estimate_digest'])
        finally:
            flow.close()

    def test_cent_boundaries_and_hostile_context(self):
        cases = [('1.00499999999999999999999999999', '1.00'),
                 ('1.005', '1.01'), ('1.00500000000000000000000000001', '1.01'),
                 ('0.00499999999999999999999999999', '0.00'), ('0.005', '0.01')]
        with localcontext() as context:
            context.prec, context.rounding, context.Emin, context.Emax = 3, ROUND_DOWN, -9, 9
            for signal in context.traps:
                context.traps[signal] = True
            context.clear_flags()
            for rate, expected in cases:
                with self.subTest(rate=rate):
                    self.assertEqual(self.complete(['1'], rate)['amount'], expected)
            self.assertEqual(self.complete(['100000'], '1000000', '100')['amount'], '200000000000.00')
            self.assertFalse(any(context.flags.values()))
            self.assertEqual((context.prec, context.rounding, context.Emin, context.Emax), (3, ROUND_DOWN, -9, 9))

    def test_thirty_high_precision_lines_match_exact_fraction_oracle(self):
        hours = ['99999.' + '9' * 64] * 30
        rate, contingency = '999999.' + '9' * 64, '99.' + '9' * 64
        result = self.complete(hours, rate, contingency)
        self.assertEqual(result['amount'], exact_cents(hours, rate, contingency))
        self.assertEqual(Fraction(result['hours']), sum(Fraction(h) for h in hours))
        self.assertEqual(self.complete(['1e-64'], '1e-64', '1e-64')['amount'], '0.00')

    def test_scale_65_is_rejected_without_partial_persistence(self):
        flow = EstimationFlow()
        try:
            tasks = [{'id': 'a', 'description': 'Fixture'}]
            for rate, contingency in (('1e-65', '0'), ('1', '1e-65')):
                with self.assertRaisesRegex(ValueError, '64 decimales'):
                    flow.propose('invalid', self.author, tasks, rate, contingency)
            self.assertEqual(flow.db.execute('SELECT COUNT(*) FROM estimation_flows').fetchone()[0], 0)
            proposal = flow.propose('valid', self.author, tasks, '1', '0')
            flow.approve_breakdown('valid', self.reviewer, proposal['plan_digest'])
            with self.assertRaisesRegex(ValueError, '64 decimales'):
                flow.submit_estimates('valid', self.estimator, proposal['plan_digest'],
                                      [{'id': 'a', 'hours': '1e-65', 'evidence': ['fixture']}])
            row = flow.db.execute('SELECT state,estimate FROM estimation_flows').fetchone()
            self.assertEqual(tuple(row), ('ESTIMATION_PENDING', None))
            candidate = flow.submit_estimates('valid', self.estimator, proposal['plan_digest'],
                                               [{'id': 'a', 'hours': None, 'evidence': []}])
            with self.assertRaisesRegex(ValueError, '64 decimales'):
                flow.approve_final('valid', self.reviewer, candidate['estimate_digest'], {'a': '1e-65'})
            self.assertEqual(flow.db.execute('SELECT state FROM estimation_flows').fetchone()[0], 'FINAL_PENDING')
        finally:
            flow.close()
