"""実験データ整理のサンプル台帳の項目（試料の種類・BNAL）と台帳の値の対応。"""
import unittest

import app


class PlanFieldTests(unittest.TestCase):
    def test_unregistered_sample_shows_unset(self):
        self.assertEqual(app.plan_choices({}), ('未設定', '未分類'))
        self.assertEqual(app.plan_summary({}), '台帳なし')

    def test_choices_and_fields_round_trip(self):
        for entry in (dict(kind='核酸', bnal='target'), dict(kind='アミノ酸', bnal='excluded')):
            self.assertEqual(app.plan_fields(*app.plan_choices(entry)), entry)

    def test_unset_choices_clear_the_fields(self):
        self.assertEqual(app.plan_fields('未設定', '未分類'), dict(kind=None, bnal=None))

    def test_summary(self):
        self.assertEqual(app.plan_summary(dict(kind='核酸', bnal='target')), '核酸・BNAL対象')
        self.assertEqual(app.plan_summary(dict(bnal='excluded')), '種類未設定・BNAL対象外')
        self.assertEqual(app.plan_summary(dict(kind='アミノ酸')), 'アミノ酸・BNAL未分類')


if __name__ == '__main__':
    unittest.main()
