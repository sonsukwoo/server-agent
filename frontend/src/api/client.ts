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
}
