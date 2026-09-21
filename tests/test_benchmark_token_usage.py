import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tools.benchmark_token_usage import UsageRecorder, summarize
from tools.benchmark_one_system import StreamResponse, StreamClient


class UsageTests(unittest.TestCase):
    def test_records_empty_reply_usage_and_failed_attempt_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "usage.jsonl"
            response = Mock(status_code=200)
            response.json.return_value = {
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20,
                          "prompt_tokens_details": {"cached_tokens": 4}},
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
            transport = Mock()
            transport.post.side_effect = [response, RuntimeError("secret credential")]
            recorder = UsageRecorder(path, transport)
            self.assertIs(recorder.post("url", headers={"Authorization": "secret"},
                                        json={"model": "test", "messages": ["private prompt"]}), response)
            with self.assertRaises(RuntimeError):
                recorder.post("url")
            data = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(data[0]["usage"]["total_tokens"], 20)
            self.assertIsNone(data[1]["usage"])
            self.assertNotIn("secret", path.read_text())
            self.assertNotIn("private prompt", path.read_text())
            summary = summarize(data)
            self.assertEqual(summary["missing_usage_attempts"], 1)
            self.assertEqual(summary["reported_token_sums"]["total_tokens"], 20)
            self.assertEqual(summary["reported_token_sums"]["prompt_tokens_details.cached_tokens"], 4)

    def test_missing_usage_is_not_zero(self):
        summary = summarize([{"usage": None, "elapsed_ms": 2}])
        self.assertEqual(summary["reported_token_sums"], {})
        self.assertEqual(summary["usage_reported_attempts"], 0)

    def test_stream_usage_uses_final_snapshot_without_double_counting(self):
        response = Mock(status_code=200)
        response.iter_lines.return_value = iter([
            'data: {"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}', '',
            'data: {"usage":{"prompt_tokens":10,"completion_tokens":7,"total_tokens":17}}', '',
            'data: [DONE]', '',
        ])
        record = {}
        list(StreamResponse(response, record).iter_lines())
        self.assertEqual(record["usage"]["total_tokens"], 17)
        self.assertTrue(record["stream_consumed"])

    def test_stream_ledger_retains_usage_on_downstream_validation_error(self):
        from contextlib import contextmanager
        response = Mock(status_code=200)
        response.iter_lines.return_value = iter(['data: {"usage":{"total_tokens":12}}', ''])
        @contextmanager
        def stream(*args, **kwargs):
            yield response
        with tempfile.TemporaryDirectory() as temp:
            recorder = UsageRecorder(Path(temp) / "usage.jsonl", Mock())
            client = Mock()
            client.stream = stream
            with self.assertRaises(ValueError):
                with StreamClient(client, recorder).stream("POST", "url", content=b'{}') as wrapped:
                    list(wrapped.iter_lines())
                    raise ValueError("validation failed")
            self.assertEqual(recorder.records[0]["usage"]["total_tokens"], 12)


if __name__ == "__main__":
    unittest.main()
