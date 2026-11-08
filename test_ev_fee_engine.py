import unittest

from ev_fee_engine import calculate_fee


class CalculateFeeTests(unittest.TestCase):
    def test_calculates_energy_and_session_fee(self):
        self.assertEqual(calculate_fee(24.5, 0.42, 1.50), 11.79)

    def test_defaults_session_fee_to_zero(self):
        self.assertEqual(calculate_fee(10, 0.30), 3.00)

    def test_rejects_negative_values(self):
        for arguments in ((-1, 0.30), (1, -0.30), (1, 0.30, -1)):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    calculate_fee(*arguments)


if __name__ == "__main__":
    unittest.main()
