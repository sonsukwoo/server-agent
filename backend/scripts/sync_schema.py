"""DB 스키마를 JSON 파일로 동기화하는 스크립트"""
import json
import os
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text, create_engine
from config.settings import settings

# DB 연결 설정
DATABASE_URL = f"postgresql://{settings.db_user}:{settings.db_password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
engine = create_engine(DATABASE_URL)

SCHEMA_DIR = Path(settings.schema_dir)
SCHEMA_DIR.mkdir(exist_ok=True)

def sync_schema():
    """DB 스키마를 JSON 파일로 추출"""
    print("=" * 60)
    print("DB 스키마 동기화 시작")
    print("=" * 60)
    
    with engine.connect() as conn:
        # 스키마별로 처리
        schemas = ['ops_metrics', 'ops_events', 'ops_runtime']
        
        for schema_name in schemas:
            print(f"\n📦 {schema_name} 스키마 처리 중...")
            
            # 테이블 목록 조회
            result = conn.execute(text(f"""
                SELECT 
                    t.table_name,
                    obj_description(('{schema_name}.' || t.table_name)::regclass) as table_comment
                FROM information_schema.tables t
                WHERE t.table_schema = '{schema_name}'
                ORDER BY t.table_name;
            """))
            
            tables = []
            for row in result:
                table_name = row[0]
                table_comment = row[1] or f"{table_name} 테이블"
                
                # 컬럼 정보 조회
                col_result = conn.execute(text(f"""
                    SELECT 
                        c.column_name,
                        c.data_type,
                        col_description(('{schema_name}.{table_name}')::regclass, c.ordinal_position) as column_comment
                    FROM information_schema.columns c
                    WHERE c.table_schema = '{schema_name}' 
                      AND c.table_name = '{table_name}'
                    ORDER BY c.ordinal_position;
                """))
                
                columns = []
                for col_row in col_result:
                    columns.append({
                        "name": col_row[0],
                        "type": col_row[1],
                        "description": col_row[2] or ""
                    })
                
                tables.append({
                    "name": table_name,
                    "full_name": f"{schema_name}.{table_name}",
                    "description": table_comment,
                    "columns": columns
                })
                
                print(f"  ✓ {table_name} ({len(columns)}개 컬럼)")
            
            # JSON 파일로 저장
            schema_data = {
                "schema_name": schema_name,
                "tables": tables
            }
            
            output_file = SCHEMA_DIR / f"{schema_name}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(schema_data, f, ensure_ascii=False, indent=2)
            
            print(f"  💾 저장: {output_file} ({len(tables)}개 테이블)")
    
    print("\n" + "=" * 60)
    print("✅ 스키마 동기화 완료!")
    print("=" * 60)

if __name__ == "__main__":
    sync_schema()
