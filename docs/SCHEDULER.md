# 스키마 동기화 스케줄러

## 📋 개요

DB 스키마를 매일 자동으로 JSON 파일로 동기화하는 스케줄러입니다.

---

## ⏰ 실행 주기

- **자동 실행**: 매일 새벽 **03:00**
- **시작 시 실행**: 스케줄러 시작 시 즉시 1회 실행

---

## 🔧 사용 방법

### 1. 수동 실행 (테스트용)

```bash
# 스케줄러 단독 실행
python src/scheduler/schema_sync.py

# 또는 스크립트 직접 실행
python scripts/sync_schema.py
```

### 2. 메인 앱에 통합

```python
# main.py 또는 app.py
from src.scheduler import scheduler

# 앱 시작 시
scheduler.start()

# 앱 종료 시
scheduler.stop()
```

---

## 📁 동기화 대상

| 스키마 | 파일 | 설명 |
|:---|:---|:---|
| `ops_metrics` | `schema/ops_metrics.json` | 시스템 메트릭 (CPU, RAM, 디스크 등) |
| `ops_events` | `schema/ops_events.json` | 시스템 이벤트 로그 |
| `ops_runtime` | `schema/ops_runtime.json` | 런타임 정보 (tmux 세션 등) |

---

## ⚙️ 동작 방식

1. **DB 연결** → PostgreSQL 접속
2. **스키마 조회** → 테이블/컬럼 정보 추출
3. **JSON 저장** → 기존 파일 **완전 덮어쓰기** (찌꺼기 없음)
4. **로그 출력** → 동기화 결과 기록

---

## 📊 로그 예시

```
2026-01-31 03:00:00 - INFO - ============================================================
2026-01-31 03:00:00 - INFO - 스키마 동기화 시작
2026-01-31 03:00:00 - INFO - ============================================================

📦 ops_metrics 스키마 처리 중...
  ✓ metrics_system (13개 컬럼)
  ✓ metrics_cpu (7개 컬럼)
  💾 저장: schema/ops_metrics.json (9개 테이블)

2026-01-31 03:00:01 - INFO - ✅ 스키마 동기화 완료!
```

---

## 🛠️ 설정 변경

### 실행 시간 변경

`src/scheduler/schema_sync.py`:
```python
# 매일 새벽 3시 → 매일 오전 9시로 변경
self.scheduler.add_job(
    run_schema_sync,
    trigger=CronTrigger(hour=9, minute=0),  # ← 여기 수정
    ...
)
```

### 실행 주기 변경

```python
# 매일 → 매주 월요일 3시
trigger=CronTrigger(day_of_week='mon', hour=3, minute=0)

# 매일 → 매시간
trigger=CronTrigger(minute=0)
```

---

## ✅ 테스트 완료

- ✅ 수동 실행 성공
- ✅ 14개 테이블 동기화 확인
- ✅ JSON 파일 덮어쓰기 확인
- ✅ 로그 출력 정상

---

## 📦 의존성

- `apscheduler>=3.10.0` (자동 설치됨)
