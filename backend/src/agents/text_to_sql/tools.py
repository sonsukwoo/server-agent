from typing import List, Dict, Any, Tuple
import logging

from .utils import build_table_context

logger = logging.getLogger("TEXT_TO_SQL_TOOLS")

def expand_tables_tool(
    current_selected: List[str],
    candidates: List[Dict[str, Any]],
    offset: int,
    batch_size: int = 5
) -> Tuple[List[str], str, int]:
    """
    후보 테이블 목록에서 다음 배치를 가져와 테이블 컨텍스트를 확장합니다.
    
    Args:
        current_selected: 현재 선택된 테이블 이름 리스트
        candidates: 전체 후보 테이블 리스트 (검색 결과)
        offset: 현재 오프셋 (어디까지 처리했는지)
        batch_size: 한 번에 추가할 테이블 수

    Returns:
        (new_selected_tables, new_table_context, new_offset)
    """
    total_candidates = len(candidates)
    
    # 1. 더 확장할 후보가 없는 경우
    if offset >= total_candidates:
        logger.info("expand_tables_tool: No more candidates to expand (offset=%d/%d)", offset, total_candidates)
        # 변경 없음
        return current_selected, "", offset

    # 2. 다음 배치 슬라이싱
    next_offset = min(offset + batch_size, total_candidates)
    next_batch_items = candidates[offset:next_offset]
    
    # 3. 새로운 테이블 이름 추출
    added_tables = [c["table_name"] for c in next_batch_items]
    
    # 4. 기존 선택 목록과 병합 (중복 제거)
    # 순서를 유지하기 위해 dict.fromkeys 등을 쓸 수 있으나, 여기선 단순 병합
    new_selected = list(dict.fromkeys(current_selected + added_tables))
    
    logger.info(
        "expand_tables_tool: Expanded %d tables (offset %d -> %d). Added: %s",
        len(added_tables), offset, next_offset, added_tables
    )
    
    # 5. 컨텍스트 재구축 (utils 사용)
    # rebuild_context_from_candidates를 사용하여 이름 매칭 및 컨텍스트 생성을 한 번에 처리
    from .utils import rebuild_context_from_candidates
    _, new_context = rebuild_context_from_candidates(candidates, new_selected)
    
    return new_selected, new_context, next_offset
