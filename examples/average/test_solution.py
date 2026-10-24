import unittest

from solution import average


class TestAverage(unittest.TestCase):
    """average 函数的正常、空列表、负数、单元素与输入不变性测试。"""

    def test_normal(self):
        """正常列表返回浮点算术平均值。"""
        self.assertEqual(average([1, 2, 3]), 2.0)
        self.assertEqual(average([1, 2]), 1.5)
        self.assertIsInstance(average([1, 2, 3]), float)

    def test_empty(self):
        """空列表返回 None，且不抛 ZeroDivisionError。"""
        self.assertIsNone(average([]))

    def test_negative(self):
        """负数与正负混合场景结果正确。"""
        self.assertEqual(average([-1, -2, -3]), -2.0)
        self.assertEqual(average([-5, 5]), 0.0)

    def test_single(self):
        """单元素返回该元素本身，类型为 float。"""
        self.assertEqual(average([7]), 7.0)
        self.assertIsInstance(average([7]), float)

    def test_input_not_modified(self):
        """调用后传入列表的内容与长度保持不变。"""
        data = [3, 1, 2]
        snapshot = list(data)
        average(data)
        self.assertEqual(data, snapshot)
        self.assertEqual(len(data), len(snapshot))

        empty = []
        average(empty)
        self.assertEqual(empty, [])


if __name__ == "__main__":
    unittest.main()
