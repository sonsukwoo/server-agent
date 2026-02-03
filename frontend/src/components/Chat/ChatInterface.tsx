import React, { useState, useRef, useEffect } from 'react';
import { Send, Plus, MessageSquare, Bot, Trash2 } from 'lucide-react';
import { MessageBubble } from './MessageBubble';
import { ApiClient } from '../../api/client';
import { ResourceDashboard } from '../Dashboard/ResourceDashboard';

export const ChatInterface: React.FC = () => {
    const [messages, setMessages] = useState<any[]>([]);
    const [inputValue, setInputValue] = useState('');
    const [sessionStates, setSessionStates] = useState<Record<string, { isLoading: boolean; status: string }>>({});
    const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
    const [sessions, setSessions] = useState<any[]>([]);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const currentSessionIdRef = useRef<string | null>(null);

    const sortSessionsDesc = (list: any[]) =>
        [...list].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());

    const ensureSessionState = (sessionId: string) => {
        setSessionStates(prev => {
            if (prev[sessionId]) return prev;
            return { ...prev, [sessionId]: { isLoading: false, status: '' } };
        });
    };

    const updateSessionState = (sessionId: string, patch: Partial<{ isLoading: boolean; status: string }>) => {
        setSessionStates(prev => {
            const baseState = prev[sessionId] ?? { isLoading: false, status: '' };
            return {
                ...prev,
                [sessionId]: { ...baseState, ...patch }
            };
        });
    };

    const loadSession = async (sessionId: string) => {
        const detail = await ApiClient.getSession(sessionId);
        setCurrentSessionId(detail.id);
        ensureSessionState(detail.id);
        setMessages(detail.messages.map(m => ({
            role: m.role,
            text: m.content,
            sqlResult: m.payload_json?.sql_result,
            visual_hint: m.payload_json?.visual_hint,
            logs: []
        })));
    };

    const refreshSessions = async () => {
        const list = await ApiClient.getSessions();
        setSessions(sortSessionsDesc(list));
    };

    // 초기 세션 로드
    useEffect(() => {
        const initSession = async () => {
            try {
                const list = await ApiClient.getSessions();
                setSessions(sortSessionsDesc(list));
                if (list.length > 0) {
                    const lastSession = sortSessionsDesc(list)[0];
                    await loadSession(lastSession.id);
                } else {
                    await startNewChat();
                }
            } catch (e) {
                console.error("Failed to load session:", e);
            }
        };
        initSession();
    }, []);

    const adjustHeight = () => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
            textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
        }
    };

    useEffect(() => {
        adjustHeight();
    }, [inputValue]);

    useEffect(() => {
        currentSessionIdRef.current = currentSessionId;
    }, [currentSessionId]);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, currentSessionId, sessionStates]);

    const handleSubmit = async (e?: React.FormEvent) => {
        if (e) e.preventDefault();
        const activeSessionId = currentSessionId;
        if (!inputValue.trim() || !activeSessionId) return;
        if (sessionStates[activeSessionId]?.isLoading) return;

        const userQuestion = inputValue.trim();
        setInputValue('');

        // 제목을 첫 질문으로 즉시 업데이트 (로컬 UI 선반영)
        setSessions(prev => prev.map(s => {
            if (s.id !== activeSessionId) return s;
            if (s.title && s.title !== 'New Chat') return s;
            const truncated = userQuestion.length <= 15 ? userQuestion : `${userQuestion.slice(0, 15)}...`;
            return { ...s, title: truncated };
        }));

        setMessages(prev => [...prev, { role: 'user', text: userQuestion }]);

        // 사용자 메시지 DB 저장 (비동기)
        ApiClient.saveMessage(activeSessionId, 'user', userQuestion).catch(console.error);

        updateSessionState(activeSessionId, { isLoading: true, status: '사용자 질문 분석 중...' });
        const capturedLogs: string[] = ['사용자 질문 분석 중...'];

        try {
            const result = await ApiClient.query(userQuestion, (newStatus) => {
                updateSessionState(activeSessionId, { status: newStatus });
                if (capturedLogs[capturedLogs.length - 1] !== newStatus) {
                    capturedLogs.push(newStatus);
                }
            });

            const assistantMsg = {
                role: 'assistant',
                text: result.data?.report || '',
                sqlResult: result.data?.raw?.sql_result,
                visual_hint: result.data?.raw?.visual_hint,
                logs: [...capturedLogs]
            };

            if (currentSessionIdRef.current === activeSessionId) {
                setMessages(prev => [...prev, assistantMsg]);
            }

            // 에이전트 응답 DB 저장
            const payload = {
                sql_result: assistantMsg.sqlResult,
                visual_hint: assistantMsg.visual_hint
            };
            ApiClient.saveMessage(activeSessionId, 'assistant', assistantMsg.text, payload).catch(console.error);
            refreshSessions().catch(console.error);
        } catch (error) {
            console.error('Failed to query:', error);
            if (currentSessionIdRef.current === activeSessionId) {
                setMessages(prev => [...prev, {
                    role: 'assistant',
                    text: `죄송합니다. 오류가 발생했습니다: ${error instanceof Error ? error.message : '알 수 없는 오류'}`
                }]);
            }
        } finally {
            updateSessionState(activeSessionId, { isLoading: false, status: '' });
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
        }
    };

    const startNewChat = async () => {
        try {
            const session = await ApiClient.createSession();
            setCurrentSessionId(session.id);
            ensureSessionState(session.id);
            setMessages([]);
            setSessions(prev => sortSessionsDesc([session, ...prev]));
        } catch (e) {
            console.error("Failed to create new chat:", e);
        }
    };

    const handleDeleteSession = async (sessionId: string) => {
        try {
            await ApiClient.deleteSession(sessionId);
            const next = sortSessionsDesc(sessions.filter(s => s.id !== sessionId));
            setSessions(next);
            setSessionStates(prev => {
                const { [sessionId]: _, ...rest } = prev;
                return rest;
            });
            if (currentSessionId === sessionId) {
                if (next.length > 0) {
                    await loadSession(next[0].id);
                } else {
                    await startNewChat();
                }
            }
        } catch (e) {
            console.error("Failed to delete session:", e);
        }
    };

    const activeSessionState = currentSessionId ? sessionStates[currentSessionId] : undefined;
    const isLoading = !!activeSessionState?.isLoading;
    const status = activeSessionState?.status || '';

    return (
        <div className="app-container">
            <aside className="sidebar">
                <div className="sidebar-new-chat" onClick={startNewChat}>
                    <Plus size={16} />
                    New chat
                </div>
                <div className="sidebar-sessions">
                    {sessions.map((s) => (
                        <div
                            key={s.id}
                            className={`sidebar-session ${s.id === currentSessionId ? 'active' : ''}`}
                            onClick={() => loadSession(s.id)}
                        >
                            <MessageSquare size={14} />
                            <span className="sidebar-session-title">{s.title}</span>
                            {sessionStates[s.id]?.isLoading && (
                                <span className="sidebar-session-loading" aria-label="loading" />
                            )}
                            <button
                                className="sidebar-session-delete"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    handleDeleteSession(s.id);
                                }}
                                aria-label="Delete session"
                                title="Delete session"
                            >
                                <Trash2 size={14} />
                            </button>
                        </div>
                    ))}
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
