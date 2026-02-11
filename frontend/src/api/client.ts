export interface QueryRequest {
    agent: string;
    question: string;
    session_id?: string;
}

export interface QueryResponse {
    ok: boolean;
    agent: string;
    data: {
        report: string;
        suggested_actions: string[];
        raw: any;
    } | null;
    error: string | null;
    // HITL 역질문
    clarification_needed?: boolean;
    clarification_message?: string;
    session_id?: string;
}

const getApiBaseUrl = () => {
    // 1. Vite 환경 변수가 명시적으로 지정된 경우만 우선 사용
    const envUrl = import.meta.env.VITE_API_BASE_URL;

    // 개발 환경 (npm run dev)에서는 8000번 포트로 직접 연결
    // window.location.port가 있으면(Vite 서버가 보통 5173 사용) 백엔드 주소를 명시함
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
        if (window.location.port === '5173' || window.location.port === '3000') {
            return envUrl || 'http://localhost:8000';
        }
    }

    // 2. 배포 환경 (Nginx 프록시 사용) 핵심 로직
    // 상대 경로 '/api'를 반환하면, 브라우저가 현재 접속한 도메인(터널 주소 등) 뒤에 
    // 자동으로 /api를 붙여서 Nginx가 백엔드로 전달할 수 있게 합니다.
    return '/api';
};

const API_BASE_URL = getApiBaseUrl();

export interface ChatSession {
    id: string;
    title: string;
    created_at: string;
    updated_at: string;
}

export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    payload_json?: any;
    created_at: string;
}

export interface ChatSessionDetail extends ChatSession {
    messages: ChatMessage[];
}

export interface ResourceSummary {
    total: number;
    active: number;
    inactive: number;
}

export interface SchemaTable {
    table: string;
    columns: string[];
}

export class ApiClient {
    private baseUrl: string;

    constructor(baseUrl: string = API_BASE_URL) {
        this.baseUrl = baseUrl;
    }

    static async query(
        question: string,
        sessionId?: string,
        onStatus?: (status: string) => void,
        signal?: AbortSignal
    ): Promise<QueryResponse> {
        const response = await fetch(`${API_BASE_URL}/query`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            signal,
            body: JSON.stringify({
                agent: 'sql',
                question,
                session_id: sessionId,
            }),
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'API request failed');
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error('ReadableStream not supported');

        const decoder = new TextDecoder();
        let finalResult: QueryResponse | null = null;
        let buffer = '';

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    const trimmedLine = line.trim();
                    if (!trimmedLine || !trimmedLine.startsWith('data: ')) continue;

                    try {
                        const data = JSON.parse(trimmedLine.slice(6));

                        if (data.type === 'status' && onStatus) {
                            onStatus(data.message);
                        } else if (data.type === 'result') {
                            finalResult = data.payload;
                        } else if (data.type === 'clarification') {
                            // 역질문 이벤트 처리
                            finalResult = {
                                ok: true,
                                agent: 'system',
                                session_id: data.session_id, // 백엔드에서 전달받은 session_id
                                data: null,
                                error: null,
                                clarification_needed: true,
                                clarification_message: data.message
                            };
                        } else if (data.type === 'error') {
                            // 에러 발생 시 즉시 throw하여 루프 탈출 및 catch 블록으로 이동
                            throw new Error(data.message);
                        }
                    } catch (e) {
                        // JSON 파싱 에러나 내부 throw된 에러를 다시 던짐
                        throw e;
                    }
                }
            }
        } catch (error) {
            // 스트림 읽기 중 에러 발생 시 (네트워크 오류, 백엔드 에러 등)
            // 이미 finalResult가 있다면(드문 경우) 무시하거나, 에러를 우선시할지 결정.
            // 여기서는 에러를 throw하여 UI에 실패를 알림.
            throw error;
        } finally {
            reader.releaseLock();
        }

        if (!finalResult) {
            // 스트림이 정상 종료되었으나 결과가 없는 경우 (백엔드 로직 오류 등)
            throw new Error('No result received from server (Stream ended without result)');
        }

        return finalResult;
    }

    async getResourceSummary(): Promise<ResourceSummary> {
        const response = await fetch(`${this.baseUrl}/resource-summary`);
        if (!response.ok) {
            throw new Error('Failed to fetch resource summary');
        }
        return response.json();
    }

    async getSchemaTables(): Promise<SchemaTable[]> {
        const response = await fetch(`${this.baseUrl}/schema/tables`);
        if (!response.ok) {
            throw new Error('Failed to fetch schema tables');
        }
        return response.json();
    }

    // -------------------------------------------------------------------------
    // Advanced Settings (Alerts)
    // -------------------------------------------------------------------------
    async createRule(rule: {
        target_table: string;
        target_column: string;
        operator: string;
        threshold: number;
        message: string;
    }): Promise<any> {
        const response = await fetch(`${this.baseUrl}/advanced/rules`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(rule)
        });
        if (!response.ok) throw new Error('Failed to create rule');
        return response.json();
    }

    async listRules(): Promise<any[]> {
        const response = await fetch(`${this.baseUrl}/advanced/rules`);
        if (!response.ok) throw new Error('Failed to fetch rules');
        return response.json();
    }

    async deleteRule(id: number): Promise<void> {
        const response = await fetch(`${this.baseUrl}/advanced/rules/${id}`, { method: 'DELETE' });
        if (!response.ok) throw new Error('Failed to delete rule');
    }

    async listAlerts(): Promise<any[]> {
        const response = await fetch(`${this.baseUrl}/advanced/alerts`);
        if (!response.ok) throw new Error('Failed to fetch alerts');
        return response.json();
    }

    async deleteAlert(id: number): Promise<void> {
        const response = await fetch(`${this.baseUrl}/advanced/alerts/${id}`, { method: 'DELETE' });
        if (!response.ok) throw new Error('Failed to delete alert');
    }

    async getSessions(): Promise<ChatSession[]> {
        const response = await fetch(`${this.baseUrl}/chat/sessions`);
        if (!response.ok) {
            throw new Error(`Failed to fetch sessions: ${response.status}`);
        }
        return response.json();
    }

    async createSession(title: string = "New Chat"): Promise<ChatSession> {
        const response = await fetch(`${this.baseUrl}/chat/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        if (!response.ok) {
            throw new Error(`Failed to create session: ${response.status}`);
        }
        return response.json();
    }

    async getSession(id: string): Promise<ChatSessionDetail> {
        const response = await fetch(`${this.baseUrl}/chat/sessions/${id}`);
        if (!response.ok) {
            throw new Error(`Failed to fetch session ${id}: ${response.status}`);
        }
        return response.json();
    }

    async deleteSession(id: string): Promise<void> {
        const response = await fetch(`${this.baseUrl}/chat/sessions/${id}`, {
            method: 'DELETE'
        });
        if (!response.ok) {
            throw new Error(`Failed to delete session ${id}: ${response.status}`);
        }
    }

    async saveMessage(sessionId: string, role: string, content: string, payload?: any): Promise<ChatMessage> {
        const response = await fetch(`${this.baseUrl}/chat/sessions/${sessionId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role, content, payload_json: payload })
        });
        if (!response.ok) {
            throw new Error(`Failed to save message: ${response.status}`);
        }
        return response.json();
    }
}
