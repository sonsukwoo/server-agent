import React, { useState, useRef, useEffect } from 'react';
import { Send, Plus, MessageSquare, Bot, Trash2, Square } from 'lucide-react';
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
    const abortControllerRef = useRef<AbortController | null>(null);

    // API Client Instance
    const apiClient = new ApiClient();

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
        const detail = await apiClient.getSession(sessionId);
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
        const list = await apiClient.getSessions();
        setSessions(sortSessionsDesc(list));
    };

    // 초기 세션 로드
    useEffect(() => {
        const initSession = async () => {
            try {
                // 기존 세션 목록만 불러오고, 특정 세션을 선택하지 않음 (새 채팅 상태)
                const list = await apiClient.getSessions();
                setSessions(sortSessionsDesc(list));
                setCurrentSessionId(null);
                setMessages([]);
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

    // 스크롤 제어 로직
    const scrollToBottom = (behavior: ScrollBehavior = 'smooth') => {
        messagesEndRef.current?.scrollIntoView({ behavior });
    };

    // 메시지가 추가되거나 세션이 바뀔 때 스크롤 처리
    // 단, 에이전트 응답 생성 중(isLoading=true)일 때는 사용자가 위를 보고 있으면 방해하지 않음
    useEffect(() => {
        // 1. 세션 변경 시에는 무조건 바닥으로
        // 2. 메시지 길이가 늘어났을 때도 바닥으로 (사용자 질문 직후 등)
        scrollToBottom();
    }, [messages.length, currentSessionId]);

    // 로딩 상태가 변할 때(메시지 생성 시작 등)는 스크롤을 강제하지 않음
    // 필요하다면 이곳에 로직 추가

    const handleSubmit = async (e?: React.FormEvent) => {
        if (e) e.preventDefault();
        if (!inputValue.trim()) return;

        let activeSessionId = currentSessionId;

        // 세션이 없으면 첫 메시지 전송 시점에 생성
        if (!activeSessionId) {
            activeSessionId = await startNewChat();
            if (!activeSessionId) return;
        }

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
        apiClient.saveMessage(activeSessionId, 'user', userQuestion).catch(console.error);

        updateSessionState(activeSessionId, { isLoading: true, status: '사용자 질문 분석 중...' });
        const capturedLogs: string[] = ['사용자 질문 분석 중...'];

        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }
        const controller = new AbortController();
        abortControllerRef.current = controller;

        try {
            const result = await ApiClient.query(userQuestion, activeSessionId, (newStatus) => {
                updateSessionState(activeSessionId, { status: newStatus });
                if (capturedLogs[capturedLogs.length - 1] !== newStatus) {
                    capturedLogs.push(newStatus);
                }
            }, controller.signal);

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
            apiClient.saveMessage(activeSessionId, 'assistant', assistantMsg.text, payload).catch(console.error);
            refreshSessions().catch(console.error);
        } catch (error) {
            if (error instanceof Error && error.name === 'AbortError') {
                console.log('Query aborted');
                if (currentSessionIdRef.current === activeSessionId) {
                    setMessages(prev => [...prev, {
                        role: 'assistant',
                        text: '요청이 중단되었습니다.',
                        logs: [...capturedLogs, '사용자에 의해 중단됨']
                    }]);
                }
            } else {
                console.error('Failed to query:', error);
                if (currentSessionIdRef.current === activeSessionId) {
                    setMessages(prev => [...prev, {
                        role: 'assistant',
                        text: `죄송합니다. 오류가 발생했습니다: ${error instanceof Error ? error.message : '알 수 없는 오류'}`
                    }]);
                }
            }
        } finally {
            updateSessionState(activeSessionId, { isLoading: false, status: '' });
            abortControllerRef.current = null;
        }
    };

    const handleStop = () => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
            abortControllerRef.current = null;
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
            const session = await apiClient.createSession();
            setCurrentSessionId(session.id);
            ensureSessionState(session.id);
            setMessages([]);
            setSessions(prev => sortSessionsDesc([session, ...prev]));
            return session.id;
        } catch (e) {
            console.error("Failed to create new chat:", e);
            return null;
        }
    };

    const handleDeleteSession = async (sessionId: string) => {
        try {
            await apiClient.deleteSession(sessionId);
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
                        {isLoading ? (
                            <button
                                type="button"
                                className="stop-button"
                                onClick={handleStop}
                                title="Stop generation"
                            >
                                <Square size={16} fill="currentColor" />
                            </button>
                        ) : (
                            <button
                                type="submit"
                                className="send-button"
                                disabled={!inputValue.trim()}
                            >
                                <Send size={16} />
                            </button>
                        )}
                    </form>
                    <p style={{ textAlign: 'center', fontSize: '12px', color: 'var(--text-secondary)', marginTop: '12px' }}>
                        Text-to-SQL Agent can make mistakes. Check important info.
                    </p>
                </div>
            </main>
        </div>
    );
};
