---
name: eng
description: 오늘의 영어 쓰기 세션(10~15분)을 시작한다. 첫 세션이면 레벨 체크, 아니면 복습 → 새 항목 → 직접 쓰기 → 교정 → 기록. 사용자가 /eng를 입력했을 때만 실행한다.
disable-model-invocation: true
allowed-tools: Read, Write(.eng-tmp/*), Bash(python tools/eng.py:*), PowerShell(python tools/eng.py:*)
---

# /eng — 오늘의 영어 세션

`CLAUDE.md`의 튜터 규칙을 그대로 따른다. 설명은 한국어, 연습 문장은 영어. 질문은 한 번에 하나.

## 0. 상태 확인

```
python tools/eng.py status
```

- 오류 메시지가 "데이터가 없습니다"이면 `python tools/eng.py init`을 실행하고 다시 `status`.
- `next_mode`가 `level-check`이면 **A. 레벨 체크**, `practice`이면 **B. 연습**으로 간다.
- 첫 메시지는 짧게: 오늘 할 일 한 줄(예: "복습 2개 → 새 표현 1개 → PR 설명 쓰기"). `practiced_today`가 true면 "오늘 이미 한 번 했어요"라고만 덧붙이고 그대로 진행한다.

## A. 레벨 체크 (첫 세션)

쉬운 것부터 어려운 것 순서로 쓰기 문제 3개를 **한 문제씩** 낸다. 상황은 한국어로 주고, 답은 영어로 받는다.

1. Slack 한 줄. 예: 팀 채널에 "스테이징 배포 끝났고, 이상 있으면 알려 달라"고 알리기.
2. 메일 2~3문장. 예: 다른 팀에 내일 장애 회고 미팅을 목요일로 미뤄 달라고 요청하기.
3. PR 설명 3~4문장. 예: 학습 파이프라인의 GPU 노드 autoscaling 설정을 바꾼 PR. 무엇을, 왜 바꿨고, 어떻게 테스트했는지.

각 답에는:
- 판정(맞음 / 일부 맞음 / 틀림)을 먼저 말하고, 가장 중요한 차이 하나와 짧은 교정본을 준다.
- 맞음이 아니면 오류 유형 하나를 정한다(`error_types` 키). 나머지 오류는 `later` 후보로 모아 둔다.
- 레벨 체크 중에는 다시 쓰기를 시키지 않는다. 바로 다음 문제로 간다.

3문제가 끝나면 걸린 시간을 묻는다("오늘 몇 분 걸렸어요? 모르면 '모름'이라고 해도 돼요"). 그다음 `.eng-tmp/payload.json`에 아래 형식으로 쓰고 기록한다.

```json
{
  "minutes": 9,
  "cefr": "B1",
  "items": [
    {"prompt_ko": "...", "answer": "사용자 답 원문", "verdict": "correct"},
    {"prompt_ko": "...", "answer": "...", "verdict": "partial", "error_type": "article",
     "right": "교정본", "note_ko": "무엇이 달랐는지 한 줄"},
    {"prompt_ko": "...", "answer": "...", "verdict": "wrong", "error_type": "tense",
     "right": "교정본", "note_ko": "..."}
  ],
  "weakness": {"type": "article", "note_ko": "약점 한 줄"},
  "later": [{"type": "tone", "text": "나중에 다룰 것 한 줄"}]
}
```

```
python tools/eng.py level --file .eng-tmp/payload.json
```

- `minutes`는 사용자가 말한 숫자. 모른다고 하면 `null`.
- `cefr`는 A1~C2 중 하나. 3문제의 결과로 정한 추정치다.
- 마지막으로 **추정 레벨과 오늘 발견한 약점 하나만** 알려 준다(예: "추정 B1이에요. 약점은 관사예요. 다음 세션부터 이걸 먼저 다룰게요."). 다른 통계나 장황한 격려는 붙이지 않는다.

## B. 연습 세션

### 1. 복습 (`due`의 항목, 최대 3개)
- 각 항목의 `prompt_ko`를 보여 주고 영어로 쓰게 한다. `answer_en`은 미리 보여 주지 않는다.
- 판정을 먼저 말한다. 의미와 핵심 표현이 맞으면 맞음, 핵심 표현은 맞는데 다른 부분이 틀렸으면 일부 맞음, 핵심을 놓쳤으면 틀림이다. 정답과 철자 하나까지 같을 필요는 없다.
- 틀림이면 정답을 보여 주고 한 번 다시 쓰게 한다. 기록은 첫 판정만 한다.
- `due`가 비어 있으면 "오늘 복습할 것은 없어요"라고 말하고 넘어간다.

### 2. 새 항목 1개
다음 순서로 고른다.
1. `later_open`에 있는 것
2. `level.weakness`나 `recent_error_types`의 상위 유형
3. `suggested_topic`에서 자주 쓰는 표현

설명은 한국어 3~4줄로 한다: 언제 쓰는지, 예문 하나(업무 맥락), 흔한 실수 하나.
세션당 새 항목은 최대 3개이고 기본은 1개다. 사용자가 더 하고 싶다고 할 때만 늘린다.

### 3. 직접 쓰기
`suggested_topic`(slack / email / pr / tech-doc) 상황을 하나 주고, 방금 배운 항목을 써서 1~3문장을 쓰게 한다. 상황에는 인프라/MLOps 맥락을 쓴다(배포, 장애, 쿠버네티스, 모델 학습/서빙, 모니터링 등). 상황은 매번 다르게 낸다.

### 4. 교정
- 판정을 먼저 말한다.
- 가장 중요한 오류 하나만 설명하고, 짧고 자연스러운 교정본을 준다.
- 다른 오류는 말하지 않고 `later`에 넣는다. 끝에 "다른 것 N개는 나중에 목록에 넣었어요" 한 줄만 쓴다.
- 문장의 의도가 애매하면 판정 전에 "~라는 뜻인가요?"라고 먼저 묻는다.
- 틀림이면 한 번 다시 쓰게 한다.

### 5. 기록
걸린 시간을 묻고, `.eng-tmp/payload.json`에 쓴 뒤 기록한다.

```json
{
  "minutes": 12,
  "topic": "pr",
  "topic_note": "Helm 차트 값 변경 PR",
  "reviews": [{"id": "r0003", "verdict": "correct"}],
  "new_items": [
    {"kind": "expression", "prompt_ko": "복습 때 보여 줄 한국어 과제 (예: 'PR 첫 줄: 이 PR은 X를 고친다')",
     "answer_en": "This PR fixes X.", "note_ko": "PR 첫 줄은 현재형 동사로 시작"}
  ],
  "errors": [
    {"type": "article", "wrong": "사용자 문장", "right": "교정본", "note_ko": "한 줄 설명", "add_to_review": true}
  ],
  "later": [{"type": "conciseness", "text": "나중에 다룰 것 한 줄"}],
  "later_done": ["L0001"]
}
```

```
python tools/eng.py session --file .eng-tmp/payload.json
```

- `verdict`: `correct` / `partial` / `wrong`.
- `kind`: `expression` 또는 `grammar`.
- `errors`에는 오늘 짚은 오류를 넣는다. 복습에 다시 나와야 할 것만 `add_to_review: true`로 한다(세션당 최대 3개).
- `later_done`: 오늘 새 항목으로 다룬 '나중에' 항목의 id.
- 스크립트가 `오류:`로 거부하면 그 내용대로 payload를 고쳐서 다시 실행한다. 직접 data/ 파일을 고치지 않는다.

기록이 끝나면 한 줄로 마무리한다. 예: "기록했어요. 오늘 배운 'push back'은 내일 복습에 나와요. 연속 3일째예요."
