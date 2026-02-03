import React, { useState } from 'react';
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
    const [isLogsOpen, setIsLogsOpen] = useState(false);
    const isAssistant = message.role === 'assistant';

    return (
        <div className={`message ${message.role}`}>
            <div className="chat-content">
                <div className="flex gap-4">
                    <div className="avatar">
                        {isAssistant ? <Bot size={20} color="#10a37f" /> : <User size={20} color="#b4b4b4" />}
                    </div>
                    <div className="message-content">
                        {message.isThinking ? (
                            <div className="thinking text-secondary">생각 중...</div>
                        ) : (
                            <>
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

                                {isAssistant && message.logs && message.logs.length > 0 && (
                                    <div className="logs-container">
                                        <button
                                            className="logs-toggle"
                                            onClick={() => setIsLogsOpen(!isLogsOpen)}
                                        >
                                            <Clock size={14} />
                                            <span>작업 히스토리 {isLogsOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</span>
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

                                {message.sqlResult && message.sqlResult.length > 0 && (
                                    <DataTable data={message.sqlResult} />
                                )}
                            </>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};
