import React, { useState, useRef, useEffect } from 'react';
import { Send, Plus, MessageSquare } from 'lucide-react';
import { MessageBubble, type MessageContent } from './MessageBubble';
import { ApiClient } from '../../api/client';

export const ChatInterface: React.FC = () => {
    const [messages, setMessages] = useState<MessageContent[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    const handleSend = async () => {
        if (!input.trim() || isLoading) return;

        const userQuestion = input.trim();
        setInput('');

        const newUserMessage: MessageContent = {
            role: 'user',
            text: userQuestion,
        };

        const thinkingMessage: MessageContent = {
            role: 'assistant',
            text: '',
            isThinking: true,
        };

        setMessages((prev) => [...prev, newUserMessage, thinkingMessage]);
        setIsLoading(true);

        try {
            const result = await ApiClient.query(userQuestion);

            setMessages((prev) => {
                const withoutThinking = prev.slice(0, -1);
                return [
                    ...withoutThinking,
                    {
                        role: 'assistant',
                        text: result.data?.report || '결과를 가져오지 못했습니다.',
                        sqlResult: result.data?.raw?.sql_result || [],
                    },
                ];
            });
        } catch (error) {
            setMessages((prev) => {
                const withoutThinking = prev.slice(0, -1);
                return [
                    ...withoutThinking,
                    {
                        role: 'assistant',
                        text: `오류가 발생했습니다: ${error instanceof Error ? error.message : '알 수 없는 오류'}`,
                    },
                ];
            });
        } finally {
            setIsLoading(false);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
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
                    {messages.length === 0 ? (
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', opacity: 0.5 }}>
                            <Bot size={48} style={{ marginBottom: '16px' }} />
                            <h2 style={{ fontSize: '24px', fontWeight: 600 }}>무엇을 도와드릴까요?</h2>
                        </div>
                    ) : (
                        messages.map((msg, i) => <MessageBubble key={i} message={msg} />)
                    )}
                    <div ref={messagesEndRef} />
                </div>

                <div className="input-container">
                    <div className="input-wrapper">
                        <textarea
                            className="input-field"
                            placeholder="SQL 에이전트에게 질문하세요..."
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={handleKeyDown}
                            rows={1}
                        />
                        <button
                            className="send-button"
                            onClick={handleSend}
                            disabled={!input.trim() || isLoading}
                        >
                            <Send size={16} />
                        </button>
                    </div>
                    <p style={{ textAlign: 'center', fontSize: '12px', color: 'var(--text-secondary)', marginTop: '12px' }}>
                        Text-to-SQL Agent can make mistakes. Check important info.
                    </p>
                </div>
            </main>
        </div>
    );
};

import { Bot } from 'lucide-react';
