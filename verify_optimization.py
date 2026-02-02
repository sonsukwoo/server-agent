
import sys
import os

# Add the current directory to sys.path to allow imports from backend
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "backend"))

try:
    print("Attempting to import graph...")
    # Adjust import path based on server-agent root
    from backend.src.agents.text_to_sql.graph import app
    print("✅ Graph compilation successful")
    
    # Check if TIMEZONE and structured_llm_fast are accessible in nodes (indirect verification)
    from backend.src.agents.text_to_sql.nodes import structured_llm_fast
    print("✅ structured_llm_fast is defined")
    
except ImportError as e:
    print(f"❌ ImportError: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
