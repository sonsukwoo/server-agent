import React from 'react';
import ReactMarkdown from 'react-markdown';
import { User, Bot } from 'lucide-react';
import { DataTable } from '../Common/DataTable';

export interface MessageContent {
    role: 'user' | 'assistant';
    text: string;
    sqlResult?: any[];
    isThinking?: boolean;
}

interface MessageBubbleProps {
    message: MessageContent;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
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
                                    <ReactMarkdown>{message.text}</ReactMarkdown>
                                </div>
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
