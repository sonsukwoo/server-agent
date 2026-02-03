export interface QueryRequest {
    agent: string;
    question: string;
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
}

const API_BASE_URL = 'http://localhost:8000';

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

export class ApiClient {
    static async query(
        question: string,
        onStatus?: (status: string) => void
    ): Promise<QueryResponse> {
        const response = await fetch(`${API_BASE_URL}/query`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                agent: 'sql',
                question,
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

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || ''; // 마지막 잘린 줄은 버퍼에 유지

            for (const line of lines) {
                const trimmedLine = line.trim();
                if (!trimmedLine || !trimmedLine.startsWith('data: ')) continue;

                try {
                    const data = JSON.parse(trimmedLine.slice(6));
                    if (data.type === 'status' && onStatus) {
                        onStatus(data.message);
                    } else if (data.type === 'result') {
                        finalResult = data.payload;
                    } else if (data.type === 'error') {
                        throw new Error(data.message);
                    }
                } catch (e) {
                    console.error('Failed to parse streaming data:', e, trimmedLine);
                    // 파싱 에러 시 다음 줄로 계속 진행
                }
            }
        }

        if (!finalResult) throw new Error('No result received from server');
        return finalResult;
    }

    static async getResourceSummary(): Promise<any> {
        const response = await fetch(`${API_BASE_URL}/resource-summary`);
        if (!response.ok) {
            throw new Error('Failed to fetch resource summary');
        }
        return response.json();
    }

    static async getSessions(): Promise<ChatSession[]> {
        const response = await fetch(`${API_BASE_URL}/chat/sessions`);
        if (!response.ok) {
            throw new Error(`Failed to fetch sessions: ${response.status}`);
        }
        return response.json();
    }

    static async createSession(title: string = "New Chat"): Promise<ChatSession> {
        const response = await fetch(`${API_BASE_URL}/chat/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        if (!response.ok) {
            throw new Error(`Failed to create session: ${response.status}`);
        }
        return response.json();
    }

    static async getSession(id: string): Promise<ChatSessionDetail> {
        const response = await fetch(`${API_BASE_URL}/chat/sessions/${id}`);
        if (!response.ok) {
            throw new Error(`Failed to fetch session ${id}: ${response.status}`);
        }
        return response.json();
    }

    static async deleteSession(id: string): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/chat/sessions/${id}`, {
            method: 'DELETE'
        });
        if (!response.ok) {
            throw new Error(`Failed to delete session ${id}: ${response.status}`);
        }
    }

    static async saveMessage(sessionId: string, role: string, content: string, payload?: any): Promise<ChatMessage> {
        const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}/messages`, {
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
