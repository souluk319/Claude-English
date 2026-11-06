#!/usr/bin/env python
"""영어 학습 데이터 CLI. data/ 폴더는 이 스크립트로만 쓴다.

표준 라이브러리만 사용한다. Windows에서는 `python tools/eng.py ...`로 실행한다.

명령:
  init                 data/ 파일을 만든다 (이미 있으면 건드리지 않음)
  status               오늘 세션에 필요한 정보를 JSON으로 출력
  level  [--file F]    레벨 체크 결과를 기록 (stdin 또는 파일의 JSON)
  session [--file F]   연습 세션 결과를 기록 (stdin 또는 파일의 JSON)
  week [--date D]      최근 7일 요약
  validate             데이터 검사

데이터 위치: 환경 변수 ENG_DATA_DIR, 없으면 <프로젝트>/data
원본 데이터: learner-profile.json, review-queue.json, sessions.jsonl
log.md는 sessions.jsonl에서 매번 다시 만든다 (사람이 읽는 용도).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = 1
MAX_DUE = 3          # 세션당 복습 개수 상한
MAX_NEW = 3          # 세션당 새 항목 상한
MAX_ERROR_ITEMS = 3  # 세션당 오류 복습 항목 상한
MAX_BACKUPS = 20
START_EASE = 2.5
MIN_EASE = 1.3

ERROR_TYPES = {
    "article": "관사",
    "tense": "시제",
    "preposition": "전치사",
    "agreement": "수일치",
    "word-order": "어순",
    "word-choice": "단어 선택",
    "collocation": "연어(어울리는 표현)",
    "tone": "톤/격식",
    "conciseness": "간결성",
    "spelling": "철자",
    "other": "기타",
}

TOPICS = {
    "slack": "Slack 메시지",
    "email": "메일",
    "pr": "PR 설명",
    "tech-doc": "기술 문서",
    "other": "기타",
}
ROTATION = ["slack", "email", "pr", "tech-doc"]

KINDS = {"expression": "표현", "grammar": "문법", "error": "오류 교정"}

# 명세서의 판정 → SM-2 품질 점수
VERDICTS = {"correct": 5, "partial": 3, "wrong": 1}
VERDICT_ALIASES = {
    "맞음": "correct", "일부 맞음": "partial", "틀림": "wrong",
    "correct": "correct", "partial": "partial", "wrong": "wrong",
}
VERDICT_LABELS = {"correct": "맞음", "partial": "일부 맞음", "wrong": "틀림"}

CEFR = ["A1", "A2", "B1", "B2", "C1", "C2"]


class EngError(Exception):
    """사용자에게 보여 줄 입력/데이터 오류."""


# ---------------------------------------------------------------- 경로, 입출력

def data_dir() -> Path:
    env = os.environ.get("ENG_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "data").resolve()


def paths(d: Path) -> dict:
    return {
        "profile": d / "learner-profile.json",
        "queue": d / "review-queue.json",
        "sessions": d / "sessions.jsonl",
        "log": d / "log.md",
        "backups": d / ".backups",
    }


def read_json(p: Path):
    with open(p, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def read_sessions(p: Path) -> list:
    if not p.exists():
        return []
    out = []
    with open(p, "r", encoding="utf-8-sig") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise EngError(f"sessions.jsonl {i}번째 줄이 JSON이 아닙니다: {e}")
    return out


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def write_atomic(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def backup(d: Path) -> None:
    ps = paths(d)
    existing = [ps[k] for k in ("profile", "queue", "sessions", "log") if ps[k].exists()]
    if not existing:
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = ps["backups"] / stamp
    target.mkdir(parents=True, exist_ok=True)
    for p in existing:
        shutil.copy2(p, target / p.name)
    snaps = sorted(x for x in ps["backups"].iterdir() if x.is_dir())
    for old in snaps[:-MAX_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)


def parse_date(s, field="date") -> date:
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except ValueError:
        raise EngError(f"{field} 형식은 YYYY-MM-DD 여야 합니다: {s!r}")


def iso(d: date) -> str:
    return d.isoformat()


# ---------------------------------------------------------------- 기본 데이터

def default_profile(today: date) -> dict:
    return {
        "schema": SCHEMA,
        "created": iso(today),
        "native_language": "ko",
        "target_language": "en",
        "explain_in": "ko",
        "practice_in": "en",
        "focus": "개발자 업무 영어 (Slack, 메일, PR 설명, 기술 문서. 인프라/MLOps 맥락 포함)",
        "skills": {"writing": "지금", "speaking": "나중에 사용자와 정함"},
        "session_minutes_target": [10, 15],
        "level": None,
    }


def default_queue() -> dict:
    return {"schema": SCHEMA, "items": []}


def load_all(d: Path) -> tuple:
    ps = paths(d)
    if not ps["profile"].exists() or not ps["queue"].exists():
        raise EngError(f"데이터가 없습니다. 먼저 `python tools/eng.py init`을 실행하세요. ({d})")
    return read_json(ps["profile"]), read_json(ps["queue"]), read_sessions(ps["sessions"])


# ---------------------------------------------------------------- 간격 반복 (SM-2)

def sm2(item: dict, verdict: str, on: date) -> dict:
    """판정 하나를 반영한 새 항목을 돌려준다 (원본은 바꾸지 않음)."""
    q = VERDICTS[verdict]
    it = dict(item)
    it["history"] = list(item.get("history", []))
    ease = float(it.get("ease", START_EASE))
    reps = int(it.get("reps", 0))
    interval = int(it.get("interval", 0))
    if q >= 3:
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 6
        else:
            interval = math.ceil(interval * ease)
        reps += 1
    else:
        reps = 0
        interval = 1
        it["lapses"] = int(it.get("lapses", 0)) + 1
    ease = max(MIN_EASE, round(ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02), 2))
    it.update(ease=ease, reps=reps, interval=interval,
              last_review=iso(on), next_review=iso(on + timedelta(days=interval)))
    it["history"].append({"date": iso(on), "verdict": verdict})
    return it


def stage(item: dict) -> str:
    if not item.get("history"):
        return "새 항목"
    iv = int(item.get("interval", 0))
    if iv < 7:
        return "익히는 중"
    if iv < 21:
        return "익숙함"
    return "숙달"


def due_items(queue: dict, today: date, limit: int = MAX_DUE) -> tuple:
    due = [it for it in queue["items"] if parse_date(it["next_review"]) <= today]
    due.sort(key=lambda it: (
        -(today - parse_date(it["next_review"])).days,   # 오래 밀린 것 먼저
        -int(it.get("lapses", 0)),                        # 많이 틀린 것 먼저
        it["id"],
    ))
    return due[:limit], len(due)


def next_id(items: list, prefix: str) -> int:
    n = 0
    for x in items:
        s = str(x.get("id", ""))
        if s.startswith(prefix) and s[len(prefix):].isdigit():
            n = max(n, int(s[len(prefix):]))
    return n + 1


def new_item(idx: int, on: date, kind: str, prompt_ko: str, answer_en: str,
             note_ko: str = "", topic: str = "other", error_type=None) -> dict:
    return {
        "id": f"r{idx:04d}",
        "kind": kind,
        "prompt_ko": prompt_ko,
        "answer_en": answer_en,
        "note_ko": note_ko,
        "topic": topic,
        "error_type": error_type,
        "added": iso(on),
        "last_review": None,
        "next_review": iso(on + timedelta(days=1)),
        "interval": 0,
        "ease": START_EASE,
        "reps": 0,
        "lapses": 0,
        "history": [],
    }


# ---------------------------------------------------------------- 통계

def streak(sessions: list, today: date) -> int:
    days = {parse_date(s["date"]) for s in sessions}
    if not days:
        return 0
    cur = today if today in days else today - timedelta(days=1)
    if cur not in days:
        return 0
    n = 0
    while cur in days:
        n += 1
        cur -= timedelta(days=1)
    return n


def later_items(sessions: list) -> tuple:
    """(아직 안 끝난 '나중에' 항목들, 끝난 id 집합)"""
    done = set()
    allitems = []
    for s in sessions:
        done.update(s.get("later_done", []))
        for x in s.get("later", []):
            allitems.append(dict(x, date=s["date"]))
    return [x for x in allitems if x["id"] not in done], done


def error_counter(sessions: list) -> Counter:
    c = Counter()
    for s in sessions:
        for e in s.get("errors", []):
            c[e["type"]] += 1
    return c


def suggested_topic(sessions: list) -> str:
    last_used = {t: "" for t in ROTATION}
    for s in sessions:
        t = s.get("topic")
        if t in last_used:
            last_used[t] = max(last_used[t], s["date"])
    return min(ROTATION, key=lambda t: (last_used[t], ROTATION.index(t)))


def window(sessions: list, end: date, days: int) -> list:
    start = end - timedelta(days=days - 1)
    return [s for s in sessions if start <= parse_date(s["date"]) <= end]


def labeled_counts(c: Counter, labels: dict, top=None) -> list:
    return [{"type": k, "label": labels.get(k, k), "count": v} for k, v in c.most_common(top)]


# ---------------------------------------------------------------- 입력 검증

def need_str(obj: dict, key: str, where: str, allow_empty=False) -> str:
    v = obj.get(key)
    if not isinstance(v, str) or (not allow_empty and not v.strip()):
        raise EngError(f"{where}.{key}: 문자열이 필요합니다")
    return v.strip()


def check_error_type(t, where: str) -> str:
    if t not in ERROR_TYPES:
        raise EngError(f"{where}: 오류 유형 {t!r}은 목록에 없습니다. 가능한 값: {', '.join(ERROR_TYPES)}")
    return t


def check_verdict(v, where: str) -> str:
    key = VERDICT_ALIASES.get(str(v).strip()) if v is not None else None
    if key is None:
        raise EngError(f"{where}: 판정은 correct/partial/wrong (맞음/일부 맞음/틀림) 중 하나여야 합니다: {v!r}")
    return key


def check_minutes(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not (0 < v <= 240):
        raise EngError("minutes: 1~240 사이 정수이거나 null(사용자가 말하지 않음)이어야 합니다")
    return v


def check_later(payload: dict, sessions: list) -> tuple:
    later_in = payload.get("later", []) or []
    if not isinstance(later_in, list):
        raise EngError("later: 목록이어야 합니다")
    n = 0
    for s in sessions:
        for x in s.get("later", []):
            if x["id"][1:].isdigit():
                n = max(n, int(x["id"][1:]))
    later = []
    for i, x in enumerate(later_in):
        w = f"later[{i}]"
        later.append({"id": f"L{n + i + 1:04d}",
                      "type": check_error_type(x.get("type"), w),
                      "text": need_str(x, "text", w)})
    open_ids = {x["id"] for x in later_items(sessions)[0]}
    done = payload.get("later_done", []) or []
    for x in done:
        if x not in open_ids:
            raise EngError(f"later_done: {x!r}는 열려 있는 '나중에' 항목이 아닙니다")
    return later, list(done)


def read_payload(args) -> dict:
    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8-sig")
    else:
        try:
            sys.stdin.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        raw = sys.stdin.read().lstrip("﻿")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise EngError(f"입력 JSON을 읽지 못했습니다: {e}")
    if not isinstance(obj, dict):
        raise EngError("입력은 JSON 객체여야 합니다")
    return obj


# ---------------------------------------------------------------- 명령 구현 (순수 함수)

def apply_level(profile: dict, sessions: list, payload: dict, today: date, redo=False) -> tuple:
    if profile.get("level") and not redo:
        raise EngError("레벨 체크는 이미 했습니다. 다시 하려면 --redo를 붙이세요")
    on = parse_date(payload.get("date", iso(today)))
    cefr = payload.get("cefr")
    if cefr not in CEFR:
        raise EngError(f"cefr: {', '.join(CEFR)} 중 하나여야 합니다")
    items_in = payload.get("items")
    if not isinstance(items_in, list) or len(items_in) != 3:
        raise EngError("items: 레벨 체크 문제 3개의 결과가 필요합니다")
    items, errors = [], []
    for i, x in enumerate(items_in):
        w = f"items[{i}]"
        v = check_verdict(x.get("verdict"), w)
        it = {"prompt_ko": need_str(x, "prompt_ko", w),
              "answer": need_str(x, "answer", w),
              "verdict": v,
              "note_ko": str(x.get("note_ko", "")).strip()}
        if v != "correct":
            et = check_error_type(x.get("error_type"), w + ".error_type")
            it["error_type"] = et
            errors.append({"type": et, "wrong": it["answer"],
                           "right": str(x.get("right", "")).strip(), "note_ko": it["note_ko"]})
        items.append(it)
    wk = payload.get("weakness") or {}
    weakness = {"type": check_error_type(wk.get("type"), "weakness.type"),
                "note_ko": need_str(wk, "note_ko", "weakness")}
    later, later_done = check_later(payload, sessions)
    new_profile = dict(profile)
    new_profile["level"] = {"cefr": cefr, "estimated": True, "date": iso(on), "weakness": weakness}
    record = {
        "n": len(sessions) + 1,
        "date": iso(on),
        "kind": "level-check",
        "minutes": check_minutes(payload.get("minutes")),
        "topic": "other",
        "topic_note": "레벨 체크",
        "level": {"cefr": cefr, "estimated": True},
        "level_items": items,
        "weakness": weakness,
        "reviews": [],
        "new_items": [],
        "errors": errors,
        "later": later,
        "later_done": later_done,
    }
    return new_profile, sessions + [record]


def apply_session(queue: dict, sessions: list, payload: dict, today: date) -> tuple:
    on = parse_date(payload.get("date", iso(today)))
    topic = payload.get("topic")
    if topic not in TOPICS:
        raise EngError(f"topic: {', '.join(TOPICS)} 중 하나여야 합니다")
    minutes = check_minutes(payload.get("minutes"))

    items = [dict(x) for x in queue["items"]]
    by_id = {x["id"]: i for i, x in enumerate(items)}

    reviews = []
    seen = set()
    for i, r in enumerate(payload.get("reviews", []) or []):
        w = f"reviews[{i}]"
        rid = r.get("id")
        if rid not in by_id:
            raise EngError(f"{w}: 복습 항목 {rid!r}이 없습니다")
        if rid in seen:
            raise EngError(f"{w}: {rid}가 두 번 들어 있습니다. 첫 판정 하나만 기록하세요")
        seen.add(rid)
        cur = items[by_id[rid]]
        if cur.get("last_review") == iso(on):
            raise EngError(f"{w}: {rid}는 {iso(on)}에 이미 복습을 기록했습니다")
        v = check_verdict(r.get("verdict"), w)
        items[by_id[rid]] = sm2(cur, v, on)
        reviews.append({"id": rid, "verdict": v})

    idx = next_id(items, "r")
    new_in = payload.get("new_items", []) or []
    if len(new_in) > MAX_NEW:
        raise EngError(f"new_items: 세션당 새 항목은 최대 {MAX_NEW}개입니다")
    new_ids = []
    for i, x in enumerate(new_in):
        w = f"new_items[{i}]"
        kind = x.get("kind")
        if kind not in ("expression", "grammar"):
            raise EngError(f"{w}.kind: expression 또는 grammar 여야 합니다")
        et = x.get("error_type")
        if et is not None:
            check_error_type(et, w + ".error_type")
        it = new_item(idx, on, kind, need_str(x, "prompt_ko", w), need_str(x, "answer_en", w),
                      str(x.get("note_ko", "")).strip(), topic, et)
        items.append(it)
        new_ids.append(it["id"])
        idx += 1

    errors = []
    err_items = 0
    for i, e in enumerate(payload.get("errors", []) or []):
        w = f"errors[{i}]"
        et = check_error_type(e.get("type"), w + ".type")
        rec = {"type": et, "wrong": need_str(e, "wrong", w), "right": need_str(e, "right", w),
               "note_ko": str(e.get("note_ko", "")).strip()}
        if e.get("add_to_review"):
            err_items += 1
            if err_items > MAX_ERROR_ITEMS:
                raise EngError(f"errors: 복습에 넣는 오류는 세션당 최대 {MAX_ERROR_ITEMS}개입니다")
            prompt = rec["note_ko"] or f"{ERROR_TYPES[et]}: 다음 문장을 고쳐 쓰세요"
            it = new_item(idx, on, "error", f"{prompt}\n> {rec['wrong']}", rec["right"],
                          rec["note_ko"], topic, et)
            items.append(it)
            new_ids.append(it["id"])
            rec["review_id"] = it["id"]
            idx += 1
        errors.append(rec)

    later, later_done = check_later(payload, sessions)
    record = {
        "n": len(sessions) + 1,
        "date": iso(on),
        "kind": "practice",
        "minutes": minutes,
        "topic": topic,
        "topic_note": str(payload.get("topic_note", "")).strip(),
        "reviews": reviews,
        "new_items": new_ids,
        "errors": errors,
        "later": later,
        "later_done": later_done,
    }
    return dict(queue, items=items), sessions + [record]


def build_status(profile: dict, queue: dict, sessions: list, today: date) -> dict:
    due, due_total = due_items(queue, today)
    open_later, _ = later_items(sessions)
    recent = window(sessions, today, 14)
    last = sessions[-1] if sessions else None
    level = profile.get("level")
    return {
        "today": iso(today),
        "next_mode": "level-check" if not level else "practice",
        "level": level,
        "due": [dict(id=x["id"], kind=x["kind"], prompt_ko=x["prompt_ko"], answer_en=x["answer_en"],
                     note_ko=x.get("note_ko", ""), error_type=x.get("error_type"),
                     stage=stage(x), overdue_days=(today - parse_date(x["next_review"])).days)
                for x in due],
        "due_total": due_total,
        "queue_size": len(queue["items"]),
        "later_open_count": len(open_later),
        "later_open": open_later[:5],
        "sessions_total": len(sessions),
        "minutes_total": sum(s.get("minutes") or 0 for s in sessions),
        "streak_days": streak(sessions, today),
        "practiced_today": any(s["date"] == iso(today) for s in sessions),
        "last_session": ({"n": last["n"], "date": last["date"], "kind": last["kind"],
                          "topic": last["topic"]} if last else None),
        "recent_error_types": labeled_counts(error_counter(recent), ERROR_TYPES, 3),
        "suggested_topic": suggested_topic(sessions),
        "limits": {"max_due": MAX_DUE, "max_new": MAX_NEW, "max_error_items": MAX_ERROR_ITEMS},
        "error_types": ERROR_TYPES,
        "topics": TOPICS,
    }


def build_week(queue: dict, sessions: list, end: date) -> dict:
    ss = window(sessions, end, 7)
    verdicts = Counter(r["verdict"] for s in ss for r in s.get("reviews", []))
    minutes = [s.get("minutes") for s in ss]
    items_by_id = {x["id"]: x for x in queue["items"]}
    new_ids = [i for s in ss for i in s.get("new_items", [])]
    return {
        "from": iso(end - timedelta(days=6)),
        "to": iso(end),
        "sessions": len(ss),
        "practice_days": len({s["date"] for s in ss}),
        "minutes_total": sum(m for m in minutes if m),
        "minutes_unknown_sessions": sum(1 for m in minutes if m is None),
        "topics": labeled_counts(Counter(s["topic"] for s in ss), TOPICS),
        "top_error_types": labeled_counts(error_counter(ss), ERROR_TYPES, 3),
        "reviews": {VERDICT_LABELS[k]: verdicts.get(k, 0) for k in VERDICTS},
        "new_items": [{"id": i, "answer_en": items_by_id[i]["answer_en"]}
                      for i in new_ids if i in items_by_id],
        "streak_days": streak(sessions, end),
    }


# ---------------------------------------------------------------- log.md

def render_log(profile: dict, queue: dict, sessions: list) -> str:
    items_by_id = {x["id"]: x for x in queue["items"]}
    out = ["# 영어 학습 기록", "",
           "> 이 파일은 `python tools/eng.py`가 `sessions.jsonl`에서 자동으로 만듭니다. 직접 고치지 마세요.",
           ""]
    lv = profile.get("level")
    out += ["## 레벨 (추정치)", ""]
    if lv:
        out.append(f"- 추정 레벨: {lv['cefr']} (추정치, {lv['date']} 측정)")
        w = lv.get("weakness") or {}
        if w:
            out.append(f"- 약점: {ERROR_TYPES.get(w.get('type'), w.get('type'))} — {w.get('note_ko', '')}")
    else:
        out.append("- 아직 측정 전")
    out += ["", "## 나중에", ""]
    open_later, _ = later_items(sessions)
    if open_later:
        for x in open_later:
            out.append(f"- [ ] {x['id']} ({ERROR_TYPES[x['type']]}) {x['text']} — {x['date']}")
    else:
        out.append("- 없음")
    out += ["", "## 세션 (최신순)", ""]
    if not sessions:
        out.append("- 아직 없음")
    for s in reversed(sessions):
        kind = "레벨 체크" if s["kind"] == "level-check" else "연습"
        out.append(f"### {s['date']} · 세션 {s['n']} · {kind}")
        out.append("")
        out.append(f"- 소요 시간: {str(s['minutes']) + '분' if s.get('minutes') else '미입력'}")
        topic = TOPICS.get(s["topic"], s["topic"])
        if s.get("topic_note"):
            topic += f" ({s['topic_note']})"
        out.append(f"- 주제: {topic}")
        if s["kind"] == "level-check":
            marks = ", ".join(VERDICT_LABELS[x["verdict"]] for x in s.get("level_items", []))
            out.append(f"- 결과: {s['level']['cefr']} (추정치) — 문제별 {marks}")
            out.append(f"- 약점: {ERROR_TYPES[s['weakness']['type']]} — {s['weakness']['note_ko']}")
        if s.get("reviews"):
            c = Counter(r["verdict"] for r in s["reviews"])
            out.append(f"- 복습: {len(s['reviews'])}개 — "
                       + ", ".join(f"{VERDICT_LABELS[k]} {c.get(k, 0)}" for k in VERDICTS))
        errs = Counter(e["type"] for e in s.get("errors", []))
        out.append("- 오류 유형: " + (", ".join(f"{ERROR_TYPES[k]} {v}" for k, v in errs.most_common())
                                   if errs else "없음"))
        news = [items_by_id[i] for i in s.get("new_items", []) if i in items_by_id]
        news = [x for x in news if x["kind"] != "error"]
        out.append("- 새로 배운 항목: " + ("; ".join(
            x["answer_en"] + (f" — {x['note_ko']}" if x.get("note_ko") else "") for x in news)
            if news else "없음"))
        for e in s.get("errors", []):
            if e.get("right"):
                out.append(f"- 교정: \"{e['wrong']}\" → \"{e['right']}\"")
        if s.get("later_done"):
            out.append(f"- 끝낸 '나중에' 항목: {', '.join(s['later_done'])}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def save(d: Path, profile=None, queue=None, sessions=None) -> None:
    """바꾼 원본을 백업 후 기록하고 log.md를 다시 만든다."""
    ps = paths(d)
    backup(d)
    cur_profile = profile if profile is not None else read_json(ps["profile"])
    cur_queue = queue if queue is not None else read_json(ps["queue"])
    cur_sessions = sessions if sessions is not None else read_sessions(ps["sessions"])
    if profile is not None:
        write_atomic(ps["profile"], dump_json(profile))
    if queue is not None:
        write_atomic(ps["queue"], dump_json(queue))
    if sessions is not None:
        write_atomic(ps["sessions"], "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in sessions))
    write_atomic(ps["log"], render_log(cur_profile, cur_queue, cur_sessions))


# ---------------------------------------------------------------- 검사

def validate(profile: dict, queue: dict, sessions: list) -> list:
    problems = []
    if profile.get("schema") != SCHEMA:
        problems.append("learner-profile.json: schema 값이 다릅니다")
    lv = profile.get("level")
    if lv and (lv.get("cefr") not in CEFR or not lv.get("estimated")):
        problems.append("learner-profile.json: level 형식이 잘못됐습니다")
    ids = set()
    for it in queue.get("items", []):
        w = f"review-queue.json {it.get('id')}"
        if it.get("id") in ids:
            problems.append(f"{w}: id 중복")
        ids.add(it.get("id"))
        if it.get("kind") not in KINDS:
            problems.append(f"{w}: kind가 잘못됐습니다")
        for k in ("added", "next_review"):
            try:
                parse_date(it.get(k), k)
            except EngError as e:
                problems.append(f"{w}: {e}")
        if float(it.get("ease", 0)) < MIN_EASE:
            problems.append(f"{w}: ease가 {MIN_EASE}보다 작습니다")
        for h in it.get("history", []):
            if h.get("verdict") not in VERDICTS:
                problems.append(f"{w}: history 판정이 잘못됐습니다")
        if it.get("error_type") is not None and it["error_type"] not in ERROR_TYPES:
            problems.append(f"{w}: error_type이 잘못됐습니다")
    for i, s in enumerate(sessions, 1):
        w = f"sessions.jsonl 세션 {i}"
        if s.get("n") != i:
            problems.append(f"{w}: 번호(n)가 {s.get('n')}입니다")
        try:
            parse_date(s.get("date"))
        except EngError as e:
            problems.append(f"{w}: {e}")
        for r in s.get("reviews", []):
            if r.get("id") not in ids:
                problems.append(f"{w}: 없는 복습 항목 {r.get('id')}")
        for e in s.get("errors", []):
            if e.get("type") not in ERROR_TYPES:
                problems.append(f"{w}: 오류 유형 {e.get('type')}이 목록에 없습니다")
    return problems


# ---------------------------------------------------------------- CLI

def out(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def cmd_init(args) -> int:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    ps = paths(d)
    today = date.today()
    created = []
    if not ps["profile"].exists():
        write_atomic(ps["profile"], dump_json(default_profile(today)))
        created.append(ps["profile"].name)
    if not ps["queue"].exists():
        write_atomic(ps["queue"], dump_json(default_queue()))
        created.append(ps["queue"].name)
    if not ps["sessions"].exists():
        write_atomic(ps["sessions"], "")
        created.append(ps["sessions"].name)
    if not ps["log"].exists():
        profile, queue, sessions = load_all(d)
        write_atomic(ps["log"], render_log(profile, queue, sessions))
        created.append(ps["log"].name)
    out({"data_dir": str(d), "created": created,
         "kept": [n for n in ("learner-profile.json", "review-queue.json", "sessions.jsonl", "log.md")
                  if n not in created]})
    return 0


def today_from(args) -> date:
    return parse_date(args.date) if getattr(args, "date", None) else date.today()


def cmd_status(args) -> int:
    profile, queue, sessions = load_all(data_dir())
    out(build_status(profile, queue, sessions, today_from(args)))
    return 0


def cmd_level(args) -> int:
    d = data_dir()
    profile, queue, sessions = load_all(d)
    new_profile, new_sessions = apply_level(profile, sessions, read_payload(args), today_from(args), args.redo)
    save(d, profile=new_profile, sessions=new_sessions)
    s = new_sessions[-1]
    out({"saved": True, "session": s["n"], "level": new_profile["level"],
         "log": str(paths(d)["log"])})
    return 0


def cmd_session(args) -> int:
    d = data_dir()
    profile, queue, sessions = load_all(d)
    if not profile.get("level"):
        raise EngError("레벨 체크가 아직 없습니다. 첫 세션은 레벨 체크로 기록하세요 (`level`)")
    new_queue, new_sessions = apply_session(queue, sessions, read_payload(args), today_from(args))
    save(d, queue=new_queue, sessions=new_sessions)
    s = new_sessions[-1]
    by_id = {x["id"]: x for x in new_queue["items"]}
    out({"saved": True, "session": s["n"],
         "reviewed": [{"id": r["id"], "verdict": VERDICT_LABELS[r["verdict"]],
                       "next_review": by_id[r["id"]]["next_review"]} for r in s["reviews"]],
         "added": [{"id": i, "next_review": by_id[i]["next_review"]} for i in s["new_items"]],
         "later_added": [x["id"] for x in s["later"]],
         "streak_days": streak(new_sessions, parse_date(s["date"])),
         "log": str(paths(d)["log"])})
    return 0


def cmd_week(args) -> int:
    _, queue, sessions = load_all(data_dir())
    out(build_week(queue, sessions, today_from(args)))
    return 0


def cmd_validate(args) -> int:
    profile, queue, sessions = load_all(data_dir())
    problems = validate(profile, queue, sessions)
    out({"ok": not problems, "problems": problems})
    return 0 if not problems else 1


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="eng.py", description="영어 학습 데이터 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    sp = sub.add_parser("status")
    sp.add_argument("--date")
    sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("level")
    sp.add_argument("--file")
    sp.add_argument("--date")
    sp.add_argument("--redo", action="store_true")
    sp.set_defaults(fn=cmd_level)
    sp = sub.add_parser("session")
    sp.add_argument("--file")
    sp.add_argument("--date")
    sp.set_defaults(fn=cmd_session)
    sp = sub.add_parser("week")
    sp.add_argument("--date")
    sp.set_defaults(fn=cmd_week)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except EngError as e:
        sys.stderr.write(f"오류: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
