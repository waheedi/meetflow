import unittest

from app.core.costing import compute_cost_usd


class TestCosting(unittest.TestCase):
    def test_compute_cost_usd_basic(self) -> None:
        # 1M input tokens at $2 + 0.5M output tokens at $8 = $6
        self.assertEqual(compute_cost_usd(1_000_000, 500_000, 2.0, 8.0), 6.0)

    def test_compute_cost_usd_rounding(self) -> None:
        self.assertEqual(compute_cost_usd(1234, 5678, 1.23, 4.56), 0.027409)


if __name__ == "__main__":
    unittest.main()
