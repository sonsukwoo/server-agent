"""세션 대화 컨텍스트 유틸리티.

Note: LangGraph Checkpointer(PostgresSaver)가 대화 맥락의 저장/복원을
     자동 관리하므로, 기존 수동 컨텍스트 조립 및 백그라운드 요약 로직은 제거되었습니다.
     이 모듈은 UI 동기화 등 보조 기능만 유지합니다.
"""
