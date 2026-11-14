import unittest

from audible_debate.llm import BackupAdapter, Generation, LLMError


class _Dead:
    model, provider = "dead-model", "openai"

    def generate(self, messages, params, timeout_s):
        raise LLMError("HTTP 502 no usable content")


class _Live:
    provider = "openai"

    def __init__(self, model):
        self.model = model
        self.calls = 0

    def generate(self, messages, params, timeout_s):
        self.calls += 1
        return Generation(raw="{}", provider="openai", model=self.model, params={}, latency_s=0.1, usage={})


class BackupTests(unittest.TestCase):
    def test_primary_first(self):
        p, b = _Live("chosen"), _Live("spare")
        g = BackupAdapter(p, [b]).generate([], {}, 5)
        self.assertEqual(g.model, "chosen")
        self.assertEqual(b.calls, 0)
        self.assertFalse((g.usage or {}).get("backup_used"))

    def test_backup_is_labelled(self):
        b = _Live("spare")
        g = BackupAdapter(_Dead(), [b]).generate([], {}, 5)
        self.assertTrue(g.usage["backup_used"])
        self.assertEqual(g.usage["chosen_model"], "dead-model")
        self.assertEqual(g.usage["backup_model"], "spare")
        self.assertIn("502", g.usage["backup_reason"])

    def test_all_dead_raises(self):
        with self.assertRaises(LLMError):
            BackupAdapter(_Dead(), [_Dead()]).generate([], {}, 5)


if __name__ == "__main__":
    unittest.main()
