import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app import App


class SelectionRestoreTest(unittest.TestCase):
    def test_close_saves_before_destroy(self):
        ui = SimpleNamespace(busy=False, pc={'role': 'transfer'}, values=lambda: {'machine': 'AXN3'}, destroy=Mock())
        with patch('app.pc_config.save') as save:
            App.close(ui)
            self.assertEqual(save.call_args.args[0]['last_selection'], {'machine': 'AXN3'})
            ui.destroy.assert_called_once()

    def test_busy_or_failed_save_keeps_window(self):
        ui = SimpleNamespace(busy=True, destroy=Mock())
        with patch('app.messagebox.showinfo'), patch('app.pc_config.save') as save:
            App.close(ui)
            save.assert_not_called()
            ui.destroy.assert_not_called()
        ui.busy, ui.pc, ui.values = False, {}, lambda: {}
        with patch('app.messagebox.showerror'), patch('app.pc_config.save', side_effect=OSError('test')):
            App.close(ui)
            ui.destroy.assert_not_called()

    def test_restore_destination_only_if_present(self):
        class Var:
            def __init__(self, value): self.value = value
            def get(self): return self.value
            def set(self, value): self.value = value
        for names in (['Sakano_00'], ['Different']):
            ui = SimpleNamespace(busy=False, vars={k:Var(v) for k,v in {'root':'unused', 'experiment':'Sakano_00', 'sample':'OXT'}.items()}, boxes={'experiment':{}, 'sample':{}}, samples=Mock())
            ui.values = lambda: {k:v.get() for k,v in ui.vars.items()}
            ui.set_choices = lambda k, values: App.set_choices(ui, k, values)
            ui.run = lambda work, done: done(names)
            App.load_destinations(ui, restore=True)
            if 'Sakano_00' in names:
                self.assertEqual(ui.vars['experiment'].get(), 'Sakano_00')
                ui.samples.assert_called_once_with(restore_sample='OXT')
            else:
                self.assertEqual(ui.vars['experiment'].get(), '')
                ui.samples.assert_not_called()
