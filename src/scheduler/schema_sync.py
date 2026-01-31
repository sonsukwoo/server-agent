"""스키마 동기화 스케줄러 - 매일 새벽 3시 자동 실행"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def run_schema_sync():
    """스키마 동기화 실행"""
    try:
        logger.info("=" * 60)
        logger.info("스키마 동기화 시작")
        logger.info("=" * 60)
        
        # sync_schema.py 실행
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        
        from scripts.sync_schema import sync_schema
        sync_schema()
        
        logger.info("✅ 스키마 동기화 완료!")
        
    except Exception as e:
        logger.error(f"❌ 스키마 동기화 실패: {e}", exc_info=True)


class SchemaScheduler:
    """스키마 동기화 스케줄러"""
    
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        
    def start(self):
        """스케줄러 시작"""
        # 매일 새벽 3시 실행
        self.scheduler.add_job(
            run_schema_sync,
            trigger=CronTrigger(hour=3, minute=0),
            id='schema_sync',
            name='Daily Schema Sync',
            replace_existing=True
        )
        
        # 시작 시 즉시 1회 실행 (선택사항)
        self.scheduler.add_job(
            run_schema_sync,
            id='schema_sync_startup',
            name='Startup Schema Sync'
        )
        
        self.scheduler.start()
        logger.info("📅 스키마 동기화 스케줄러 시작 (매일 03:00)")
        
    def stop(self):
        """스케줄러 중지"""
        self.scheduler.shutdown()
        logger.info("스케줄러 중지")


# 전역 스케줄러 인스턴스
scheduler = SchemaScheduler()


async def main():
    """테스트용 메인 함수"""
    scheduler.start()
    
    try:
        # 무한 대기
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("종료 신호 수신")
        scheduler.stop()


if __name__ == "__main__":
    asyncio.run(main())
