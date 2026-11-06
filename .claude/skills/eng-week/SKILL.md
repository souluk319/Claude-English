---
name: eng-week
description: 최근 7일의 영어 연습을 요약한다(연습 횟수, 시간, 주제, 자주 틀린 것, 새로 배운 항목). 사용자가 /eng-week를 입력하거나 "이번 주 영어 어땠어?"처럼 주간 요약을 물을 때 쓴다. 읽기 전용.
allowed-tools: Bash(python tools/eng.py:*), PowerShell(python tools/eng.py:*)
---

# /eng-week — 주간 요약

```
python tools/eng.py week
```

결과 JSON을 한국어로 짧게 요약한다. 데이터는 바꾸지 않는다.

```
이번 주 (11/01 ~ 11/07)
- 연습: 5회, 4일 (총 58분, 시간 미입력 1회)
- 주제: PR 설명 2, Slack 메시지 2, 메일 1
- 자주 틀린 것: 관사 4, 전치사 2, 시제 1
- 복습: 맞음 6 / 일부 맞음 2 / 틀림 1
- 새로 배운 항목: push back, This PR fixes ..., ...
- 연속: 3일
```

마지막에 다음 주에 집중할 것 **하나만** 제안한다. 가장 많이 틀린 유형을 기준으로 한다.
기록이 없으면 "이번 주 기록이 없어요. /eng로 시작해 보세요."라고만 한다.
