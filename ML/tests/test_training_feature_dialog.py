"""Feature dialog edits must preserve hidden selections and support cancellation."""
from contextlib import ExitStack
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app_tk'),
               str(Path(__file__).resolve().parents[1])]
import training_features as dialog_module


class Variable:
    def __init__(self):
        self.value = ''
        self.callback = lambda *_: None

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.callback()

    def trace_add(self, mode, callback):
        self.callback = callback


class Choices:
    def __init__(self, *args, on_change=None, **kwargs):
        self._on_change = on_change
        self.items = []
        self.selected = set()
        self.listbox = self

    def pack(self, **kwargs):
        pass

    def set_items(self, items, **kwargs):
        self.items = items
        self.selected.clear()
        self._changed()

    def selection_set(self, index):
        self.selected.add(self.items[index])

    def _changed(self):
        if self._on_change:
            self._on_change(list(self.selected))


def open_dialog(selected=None):
    stack = ExitStack()
    buttons = {}
    def button(*args, **kwargs):
        buttons[kwargs['text']] = kwargs['command']
        return Mock()
    columns = ['duration', 'wave_0', 'value__mean']
    variable = Variable()
    choices = Choices()
    def checklist(*args, **kwargs):
        choices._on_change = kwargs['on_change']
        return choices
    stack.enter_context(patch.object(dialog_module, 'feature_catalog', return_value=columns))
    stack.enter_context(patch.object(dialog_module.tk, 'StringVar', return_value=variable))
    window = stack.enter_context(patch.object(dialog_module.tk, 'Toplevel')).return_value
    stack.enter_context(patch.object(dialog_module, 'CheckList', side_effect=checklist))
    for name in ('Label', 'Entry', 'Frame'):
        stack.enter_context(patch.object(dialog_module.ttk, name))
    stack.enter_context(patch.object(dialog_module.ttk, 'Button', side_effect=button))
    saved = Mock()
    dialog_module.choose_training_features(Mock(), 'rmc', 'minimal', selected, saved)
    return stack, buttons, variable, choices, window, saved


def test_search_and_bulk_exclusion_preserve_hidden_selection():
    stack, buttons, query, choices, window, saved = open_dialog()
    with stack:
        query.set('wave')
        buttons['表示中をすべて除外']()
        query.set('value')
        assert choices.selected == {'value__mean'}
        buttons['保存して閉じる']()
        saved.assert_called_once_with(['duration', 'value__mean'])
        window.destroy.assert_called_once()


def test_empty_selection_is_rejected_and_cancel_does_not_save():
    stack, buttons, query, choices, window, saved = open_dialog(['duration'])
    with stack, patch.object(dialog_module.messagebox, 'showerror') as error:
        buttons['表示中をすべて除外']()
        buttons['保存して閉じる']()
        error.assert_called_once()
        window.destroy.assert_not_called()
        buttons['キャンセル']()
        saved.assert_not_called()


def test_selecting_all_restores_default_behavior():
    stack, buttons, query, choices, window, saved = open_dialog(['duration'])
    with stack:
        buttons['表示中をすべて使用']()
        buttons['保存して閉じる']()
        saved.assert_called_once_with(None)
