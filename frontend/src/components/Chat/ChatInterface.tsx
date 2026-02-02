import React, { useState, useRef, useEffect } from 'react';
import { Send, Plus, MessageSquare, Bot } from 'lucide-react';
import { MessageBubble } from './MessageBubble';
import { ApiClient } from '../../api/client';
import { ResourceDashboard } from '../Dashboard/ResourceDashboard';

export const ChatInterface: React.FC = () => {
    const [messages, setMessages] = useState<any[]>([]);
    const [inputValue, setInputValue] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [status, setStatus] = useState<string>('');
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const adjustHeight = () => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
            textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
        }
    };

    useEffect(() => {
        adjustHeight();
    }, [inputValue]);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, status]);

    const handleSubmit = async (e?: React.FormEvent) => {
        if (e) e.preventDefault();
        if (!inputValue.trim() || isLoading) return;

        const userQuestion = inputValue.trim();
        setInputValue('');

        setMessages(prev => [...prev, { role: 'user', text: userQuestion }]);
        setIsLoading(true);
        setStatus('사용자 질문 분석 중...');
        const capturedLogs: string[] = ['사용자 질문 분석 중...'];

        try {
            const result = await ApiClient.query(userQuestion, (newStatus) => {
                setStatus(newStatus);
                if (capturedLogs[capturedLogs.length - 1] !== newStatus) {
                    capturedLogs.push(newStatus);
                }
            });

            setMessages(prev => [...prev, {
                role: 'assistant',
                text: result.data?.report || '',
                sqlResult: result.data?.raw?.sql_result,
                logs: [...capturedLogs]
            }]);
        } catch (error) {
            console.error('Failed to query:', error);
            setMessages(prev => [...prev, {
                role: 'assistant',
                text: `죄송합니다. 오류가 발생했습니다: ${error instanceof Error ? error.message : '알 수 없는 오류'}`
            }]);
        } finally {
            setIsLoading(false);
            setStatus('');
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
        }
    };

    const startNewChat = () => {
        setMessages([]);
    };

    return (
        <div className="app-container">
            <aside className="sidebar">
                <div className="sidebar-new-chat" onClick={startNewChat}>
                    <Plus size={16} />
                    New chat
                </div>
                <div style={{ marginTop: '20px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <div style={{ padding: '8px 12px', borderRadius: '6px', backgroundColor: 'var(--glass)', fontSize: '14px', display: 'flex', gap: '10px', alignItems: 'center' }}>
                        <MessageSquare size={14} />
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>Text to SQL Query</span>
                    </div>
                </div>
            </aside>

            <main className="main-area">
                <div className="chat-container">
                    <ResourceDashboard />
                    {messages.length === 0 ? (
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', opacity: 0.5 }}>
                            <Bot size={48} style={{ marginBottom: '16px' }} />
                            <h2 style={{ fontSize: '24px', fontWeight: 600 }}>무엇을 도와드릴까요?</h2>
                        </div>
                    ) : (
                        messages.map((msg, i) => <MessageBubble key={i} message={msg} />)
                    )}

                    {isLoading && (
                        <div className="message assistant thinking">
                            <div className="chat-content">
                                <div className="flex gap-4">
                                    <div className="avatar">
                                        <Bot size={20} color="#10a37f" />
                                    </div>
                                    <div className="message-content">
                                        <div className="thinking-text">
                                            {status}
                                            <span className="dot-animation">...</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    <div ref={messagesEndRef} />
                </div>

                <div className="input-container">
                    <form className="input-wrapper" onSubmit={handleSubmit}>
                        <textarea
                            ref={textareaRef}
                            className="input-field"
                            placeholder="SQL 에이전트에게 질문하세요..."
                            value={inputValue}
                            onChange={(e) => setInputValue(e.target.value)}
                            onKeyDown={handleKeyDown}
                            rows={1}
                        />
                        <button
                            type="submit"
                            className="send-button"
                            disabled={!inputValue.trim() || isLoading}
                        >
                            <Send size={16} />
                        </button>
                    </form>
                    <p style={{ textAlign: 'center', fontSize: '12px', color: 'var(--text-secondary)', marginTop: '12px' }}>
                        Text-to-SQL Agent can make mistakes. Check important info.
                    </p>
                </div>
            </main>
        </div>
    );
};
