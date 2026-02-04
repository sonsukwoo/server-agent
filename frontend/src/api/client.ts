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

export interface ResourceSummary {
    total: number;
    active: number;
    inactive: number;
}

export class ApiClient {
    private baseUrl: string;

    constructor(baseUrl: string = API_BASE_URL) {
        this.baseUrl = baseUrl;
    }

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
                    } else if (data.type === 'error') {
                        throw new Error(data.message);
                    }
                } catch (e) {
                    console.error('Failed to parse streaming data:', e, trimmedLine);
                }
            }
        }

        if (!finalResult) throw new Error('No result received from server');
        return finalResult;
    }

    async getResourceSummary(): Promise<ResourceSummary> {
        const response = await fetch(`${this.baseUrl}/resource-summary`);
        if (!response.ok) {
            throw new Error('Failed to fetch resource summary');
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
