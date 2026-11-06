"""tools/eng.py 단위 테스트. 실행: python -m unittest discover -s tools -v"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eng  # noqa: E402

D = date(2026, 11, 6)


def fresh_item(**kw):
    it = eng.new_item(1, D - timedelta(days=1), "expression", "과제", "answer")
    it.update(kw)
    return it


LEVEL_PAYLOAD = {
    "minutes": 8,
    "cefr": "B1",
    "items": [
        {"prompt_ko": "Slack: 배포 끝났다고 알리기", "answer": "Deploy is done.", "verdict": "맞음"},
        {"prompt_ko": "메일: 일정 연기 요청", "answer": "I want to delay meeting.", "verdict": "일부 맞음",
         "error_type": "article", "right": "Could we push the meeting back?", "note_ko": "관사 누락"},
        {"prompt_ko": "PR 설명 쓰기", "answer": "This PR fix bug.", "verdict": "틀림",
         "error_type": "agreement", "right": "This PR fixes a bug.", "note_ko": "3인칭 단수"},
    ],
    "weakness": {"type": "article", "note_ko": "단수 명사 앞 관사를 자주 빠뜨림"},
    "later": [{"type": "tone", "text": "요청 메일을 더 부드럽게"}],
}


class SM2Test(unittest.TestCase):
    def test_correct_sequence(self):
        it = fresh_item()
        it = eng.sm2(it, "correct", D)
        self.assertEqual((it["interval"], it["reps"], it["ease"]), (1, 1, 2.6))
        self.assertEqual(it["next_review"], "2026-11-07")
        it = eng.sm2(it, "correct", D + timedelta(days=1))
        self.assertEqual((it["interval"], it["reps"], it["ease"]), (6, 2, 2.7))
        it = eng.sm2(it, "correct", D + timedelta(days=7))
        self.assertEqual(it["interval"], 17)  # ceil(6 * 2.7) = ceil(16.2)
        self.assertEqual(it["ease"], 2.8)
        self.assertEqual(len(it["history"]), 3)

    def test_partial_passes_but_lowers_ease(self):
        it = eng.sm2(fresh_item(), "partial", D)
        self.assertEqual((it["interval"], it["reps"], it["ease"]), (1, 1, 2.36))

    def test_wrong_resets(self):
        it = fresh_item(reps=2, interval=6, ease=2.7)
        it = eng.sm2(it, "wrong", D)
        self.assertEqual((it["interval"], it["reps"], it["lapses"]), (1, 0, 1))
        self.assertEqual(it["ease"], 2.16)
        # 틀린 뒤에는 1일 → 6일 순서로 처음부터 다시 쌓인다
        it = eng.sm2(it, "correct", D + timedelta(days=1))
        self.assertEqual(it["interval"], 1)

    def test_ease_floor(self):
        it = fresh_item()
        for i in range(5):
            it = eng.sm2(it, "wrong", D + timedelta(days=i))
        self.assertEqual(it["ease"], eng.MIN_EASE)

    def test_does_not_mutate_input(self):
        it = fresh_item()
        eng.sm2(it, "correct", D)
        self.assertEqual(it["reps"], 0)
        self.assertEqual(it["history"], [])

    def test_stage(self):
        self.assertEqual(eng.stage(fresh_item()), "새 항목")
        self.assertEqual(eng.stage(fresh_item(history=[1], interval=6)), "익히는 중")
        self.assertEqual(eng.stage(fresh_item(history=[1], interval=17)), "익숙함")
        self.assertEqual(eng.stage(fresh_item(history=[1], interval=40)), "숙달")


class DueTest(unittest.TestCase):
    def test_cap_and_order(self):
        q = {"items": [
            fresh_item(id="r0001", next_review="2026-11-06", lapses=0),
            fresh_item(id="r0002", next_review="2026-11-01", lapses=0),
            fresh_item(id="r0003", next_review="2026-11-06", lapses=3),
            fresh_item(id="r0004", next_review="2026-11-04", lapses=0),
            fresh_item(id="r0005", next_review="2026-11-07", lapses=9),
        ]}
        due, total = eng.due_items(q, D)
        self.assertEqual(total, 4)
        self.assertEqual([x["id"] for x in due], ["r0002", "r0004", "r0003"])


class StreakTest(unittest.TestCase):
    def s(self, *days):
        return [{"date": d} for d in days]

    def test_cases(self):
        self.assertEqual(eng.streak([], D), 0)
        self.assertEqual(eng.streak(self.s("2026-11-06", "2026-11-06"), D), 1)  # 같은 날 두 번
        self.assertEqual(eng.streak(self.s("2026-11-04", "2026-11-05", "2026-11-06"), D), 3)
        self.assertEqual(eng.streak(self.s("2026-11-04", "2026-11-05"), D), 2)  # 오늘 아직 안 함
        self.assertEqual(eng.streak(self.s("2026-11-01", "2026-11-04"), D), 0)  # 끊김


class FlowTest(unittest.TestCase):
    """level → session → week 를 임시 데이터 폴더에서 CLI로 돌린다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ENG_DATA_DIR"] = self.tmp.name
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        os.environ.pop("ENG_DATA_DIR", None)
        self.tmp.cleanup()

    def run_cli(self, *argv, payload=None):
        if payload is not None:
            f = self.dir / "payload.json"
            f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            argv = argv + ("--file", str(f))
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            code = eng.main(list(argv))
        return code, (json.loads(o.getvalue()) if o.getvalue() else None), e.getvalue()

    def test_init_does_not_overwrite(self):
        code, res, _ = self.run_cli("init")
        self.assertEqual(code, 0)
        self.assertEqual(len(res["created"]), 4)
        prof = self.dir / "learner-profile.json"
        prof.write_text(prof.read_text(encoding="utf-8").replace('"ko"', '"KO-EDITED"', 1), encoding="utf-8")
        code, res, _ = self.run_cli("init")
        self.assertEqual(res["created"], [])
        self.assertIn("KO-EDITED", prof.read_text(encoding="utf-8"))

    def test_full_flow(self):
        self.run_cli("init")
        code, st, _ = self.run_cli("status", "--date", "2026-11-06")
        self.assertEqual(st["next_mode"], "level-check")

        # 레벨 체크 전에는 연습 세션을 받지 않는다
        code, _, err = self.run_cli("session", "--date", "2026-11-06", payload={"topic": "slack", "minutes": 5})
        self.assertEqual(code, 2)
        self.assertIn("레벨 체크", err)

        code, res, err = self.run_cli("level", "--date", "2026-11-06", payload=LEVEL_PAYLOAD)
        self.assertEqual(code, 0, err)
        self.assertTrue(res["level"]["estimated"])
        code, _, err = self.run_cli("level", "--date", "2026-11-06", payload=LEVEL_PAYLOAD)
        self.assertEqual(code, 2)  # 두 번째는 --redo 없이는 거부

        session = {
            "minutes": 12, "topic": "pr", "topic_note": "Terraform 모듈 PR",
            "new_items": [{"kind": "expression", "prompt_ko": "'이 PR은 ~를 고친다'를 PR 설명 첫 줄로",
                           "answer_en": "This PR fixes ...", "note_ko": "PR 첫 줄은 현재형"}],
            "errors": [{"type": "article", "wrong": "I updated config.", "right": "I updated the config.",
                        "note_ko": "특정한 설정 파일엔 the", "add_to_review": True}],
            "later": [{"type": "conciseness", "text": "문장을 더 짧게"}],
            "later_done": ["L0001"],
        }
        code, res, err = self.run_cli("session", "--date", "2026-11-06", payload=session)
        self.assertEqual(code, 0, err)
        self.assertEqual([a["next_review"] for a in res["added"]], ["2026-11-07", "2026-11-07"])

        code, st, _ = self.run_cli("status", "--date", "2026-11-07")
        self.assertEqual(st["next_mode"], "practice")
        self.assertEqual(st["due_total"], 2)
        self.assertEqual(st["later_open_count"], 1)
        self.assertEqual(st["later_open"][0]["id"], "L0002")
        self.assertEqual(st["streak_days"], 1)
        self.assertEqual(st["suggested_topic"], "slack")

        review = {"minutes": 10, "topic": "slack",
                  "reviews": [{"id": "r0001", "verdict": "맞음"}, {"id": "r0002", "verdict": "wrong"}]}
        code, res, err = self.run_cli("session", "--date", "2026-11-07", payload=review)
        self.assertEqual(code, 0, err)
        self.assertEqual(res["streak_days"], 2)
        q = json.loads((self.dir / "review-queue.json").read_text(encoding="utf-8"))
        r1 = [x for x in q["items"] if x["id"] == "r0001"][0]
        self.assertEqual(r1["next_review"], "2026-11-08")

        # 같은 날 같은 항목을 다시 기록하면 거부
        code, _, err = self.run_cli("session", "--date", "2026-11-07", payload=review)
        self.assertEqual(code, 2)

        code, wk, _ = self.run_cli("week", "--date", "2026-11-07")
        self.assertEqual(wk["sessions"], 3)
        self.assertEqual(wk["minutes_total"], 30)
        self.assertEqual(wk["top_error_types"][0]["type"], "article")  # 레벨 체크 1 + 세션 1
        self.assertEqual(wk["reviews"], {"맞음": 1, "일부 맞음": 0, "틀림": 1})

        log = (self.dir / "log.md").read_text(encoding="utf-8")
        self.assertIn("추정 레벨: B1 (추정치", log)
        self.assertIn("### 2026-11-07 · 세션 3 · 연습", log)
        self.assertIn("- 소요 시간: 12분", log)
        self.assertIn("- 주제: PR 설명 (Terraform 모듈 PR)", log)
        self.assertIn("- 새로 배운 항목: This PR fixes ... — PR 첫 줄은 현재형", log)
        self.assertIn("L0002 (간결성) 문장을 더 짧게", log)
        self.assertNotIn("L0001 (톤", log.split("## 세션")[0])  # 끝낸 항목은 '나중에'에서 빠짐
        self.assertLess(log.index("세션 3"), log.index("세션 1"))  # 최신순

        code, v, _ = self.run_cli("validate")
        self.assertEqual(code, 0, v)
        self.assertTrue(any((self.dir / ".backups").iterdir()))

    def test_rejects_bad_input(self):
        self.run_cli("init")
        self.run_cli("level", "--date", "2026-11-06", payload=LEVEL_PAYLOAD)
        bad = [
            {"topic": "twitter", "minutes": 5},
            {"topic": "slack", "minutes": 0},
            {"topic": "slack", "minutes": "10"},
            {"topic": "slack", "minutes": 5, "reviews": [{"id": "r9999", "verdict": "correct"}]},
            {"topic": "slack", "minutes": 5, "errors": [{"type": "grammar", "wrong": "a", "right": "b"}]},
            {"topic": "slack", "minutes": 5, "new_items": [
                {"kind": "expression", "prompt_ko": "p", "answer_en": "a"}] * 4},
            {"topic": "slack", "minutes": 5, "later_done": ["L0099"]},
        ]
        for p in bad:
            code, _, err = self.run_cli("session", "--date", "2026-11-06", payload=p)
            self.assertEqual(code, 2, (p, err))
        # 거부된 입력은 아무것도 기록하지 않는다
        lines = (self.dir / "sessions.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)

    def test_minutes_unknown(self):
        self.run_cli("init")
        self.run_cli("level", "--date", "2026-11-06", payload=dict(LEVEL_PAYLOAD, minutes=None))
        log = (self.dir / "log.md").read_text(encoding="utf-8")
        self.assertIn("- 소요 시간: 미입력", log)


if __name__ == "__main__":
    unittest.main()
