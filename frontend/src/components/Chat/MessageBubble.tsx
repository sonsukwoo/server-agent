import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import { User, Bot, ChevronDown, ChevronUp, Clock } from 'lucide-react';
import { DataTable } from '../Common/DataTable';

export interface MessageContent {
    role: 'user' | 'assistant';
    text: string;
    sqlResult?: any[];
    isThinking?: boolean;
    logs?: string[];
}

interface MessageBubbleProps {
    message: MessageContent;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
    const isAssistant = message.role === 'assistant';
    // 로그가 있고, 생각이 끝난 결과물이라면 기본적으로 열어둠 (가시성 확보)
    // useEffect를 통한 업데이트 대신 초기값 설정에 더 집중
    const [isLogsOpen, setIsLogsOpen] = useState(!!(isAssistant && message.logs && message.logs.length > 0 && !message.isThinking));

    // Props가 바뀌어 다시 렌더링될 때 logs가 추가될 수 있으므로 동기화 (선택적)
    useEffect(() => {
        if (message.logs && message.logs.length > 0 && !message.isThinking) {
            setIsLogsOpen(true);
        }
    }, [message.logs?.length, message.isThinking]);

    return (
        <div className={`message ${message.role}`}>
            <div className="chat-content">
                <div className="flex gap-4">
                    <div className="avatar">
                        {isAssistant ? <Bot size={20} color="#10a37f" /> : <User size={20} color="#b4b4b4" />}
                    </div>
                    <div className="message-content">
                        {isAssistant && message.logs && message.logs.length > 0 && (
                            <div className="logs-container">
                                <button
                                    className={`logs-toggle ${message.isThinking ? 'thinking' : ''}`}
                                    onClick={() => setIsLogsOpen(!isLogsOpen)}
                                >
                                    <Clock size={14} />
                                    <span className="flex-1 text-left">
                                        {message.isThinking ? (
                                            <>
                                                {message.text}
                                                <span className="dot-animation">...</span>
                                            </>
                                        ) : (
                                            '작업 히스토리'
                                        )}
                                    </span>
                                    {isLogsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                                </button>

                                {isLogsOpen && (
                                    <div className="logs-list">
                                        {message.logs.map((log, idx) => (
                                            <div key={idx} className="log-item">
                                                <span className="log-dot">•</span>
                                                <span className="log-text">{log}</span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {!message.isThinking && (
                            <div className="markdown-body">
                                <ReactMarkdown
                                    components={{
                                        code({ inline, className, children }: any) {
                                            if (inline) {
                                                return <code className={className}>{children}</code>;
                                            }
                                            return (
                                                <details className="code-block">
                                                    <summary>SQL 보기</summary>
                                                    <pre>
                                                        <code className={className}>{children}</code>
                                                    </pre>
                                                </details>
                                            );
                                        }
                                    }}
                                >
                                    {message.text}
                                </ReactMarkdown>
                            </div>
                        )}

                        {message.sqlResult && message.sqlResult.length > 0 && (
                            <DataTable data={message.sqlResult} />
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};
