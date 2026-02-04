import React, { useState, useEffect } from 'react';
import { ApiClient } from '../api/client';
import { X, Trash2, Puzzle, History, Plus } from 'lucide-react';

const apiClient = new ApiClient();

interface AlertRule {
    id: number;
    target_table: string;
    target_column: string;
    operator: string;
    threshold: number;
    message_template: string;
    created_at: string;
}

interface AlertHistory {
    id: number;
    rule_id: number;
    message: string;
    value: number;
    created_at: string;
}

interface SettingsModalProps {
    isOpen: boolean;
    onClose: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, onClose }) => {
    const [activeTab, setActiveTab] = useState<'rules' | 'history'>('rules');
    const [rules, setRules] = useState<AlertRule[]>([]);
    const [alerts, setAlerts] = useState<AlertHistory[]>([]);

    // New Rule Form State
    const [targetTable, setTargetTable] = useState('ops_metrics.metrics_cpu');
    const [targetColumn, setTargetColumn] = useState('cpu_percent');
    const [operator, setOperator] = useState('>');
    const [threshold, setThreshold] = useState<number>(0);
    const [message, setMessage] = useState('');

    useEffect(() => {
        if (isOpen) {
            fetchRules();
            fetchAlerts();
        }
    }, [isOpen]);

    const fetchRules = async () => {
        try {
            const data = await apiClient.listRules();
            setRules(data);
        } catch (e) {
            console.error(e);
        }
    };

    const fetchAlerts = async () => {
        try {
            const data = await apiClient.listAlerts();
            setAlerts(data);
        } catch (e) {
            console.error(e);
        }
    };

    const handleCreateRule = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            await apiClient.createRule({
                target_table: targetTable,
                target_column: targetColumn,
                operator,
                threshold,
                message
            });
            alert("규칙이 생성되었습니다!");
            setTargetTable('ops_metrics.metrics_cpu'); // Reset default
            setThreshold(0);
            setMessage('');
            fetchRules();
        } catch (e) {
            alert("규칙 생성 실패: " + e);
        }
    };

    const handleDeleteRule = async (id: number) => {
        if (!confirm("정말 이 규칙을 삭제하시겠습니까?")) return;
        try {
            await apiClient.deleteRule(id);
            fetchRules();
        } catch (e) {
            alert("삭제 실패: " + e);
        }
    };

    const handleDeleteAlert = async (id: number) => {
        if (!confirm("이 알림 기록을 삭제하시겠습니까?")) return;
        try {
            await apiClient.deleteAlert(id);
            fetchAlerts();
        } catch (e) {
            alert("삭제 실패: " + e);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="modal-overlay">
            <div className="modal-content">
                <div className="modal-header">
                    <h2>⚠️ 고급 알림 설정</h2>
                    <button className="close-button" onClick={onClose}>
                        <X size={20} />
                    </button>
                </div>

                <div className="modal-body">
                    <div className="tabs-nav">
                        <button
                            className={`tab-button ${activeTab === 'rules' ? 'active' : ''}`}
                            onClick={() => setActiveTab('rules')}
                        >
                            <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                <Puzzle size={16} />
                                모니터링 규칙 (Lego Blocks)
                            </span>
                        </button>
                        <button
                            className={`tab-button ${activeTab === 'history' ? 'active' : ''}`}
                            onClick={() => setActiveTab('history')}
                        >
                            <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                <History size={16} />
                                알림 이력 (History)
                            </span>
                        </button>
                    </div>

                    {activeTab === 'rules' && (
                        <div>
                            {/* 1. Rule Creation Form */}
                            <div className="form-section">
                                <h3><Plus size={16} /> 새 규칙 블럭 추가</h3>
                                <form onSubmit={handleCreateRule} className="form-grid">
                                    <div className="form-group">
                                        <label className="form-label">대상 테이블 (Table)</label>
                                        <input
                                            className="form-input"
                                            value={targetTable} onChange={e => setTargetTable(e.target.value)}
                                            placeholder="예: ops_metrics.metrics_cpu" required
                                        />
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">대상 컬럼 (Column)</label>
                                        <input
                                            className="form-input"
                                            value={targetColumn} onChange={e => setTargetColumn(e.target.value)}
                                            placeholder="예: cpu_percent" required
                                        />
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">조건 (Operator)</label>
                                        <select
                                            className="form-select"
                                            value={operator} onChange={e => setOperator(e.target.value)}
                                        >
                                            <option value=">">&gt; (초과)</option>
                                            <option value="<">&lt; (미만)</option>
                                            <option value=">=">&ge; (이상)</option>
                                            <option value="<=">&le; (이하)</option>
                                            <option value="=">= (같음)</option>
                                        </select>
                                    </div>
                                    <div className="form-group">
                                        <label className="form-label">임계값 (Threshold)</label>
                                        <input
                                            type="number" step="0.01" className="form-input"
                                            value={threshold} onChange={e => setThreshold(parseFloat(e.target.value))} required
                                        />
                                    </div>
                                    <div className="form-group full-width">
                                        <label className="form-label">알림 메시지 템플릿</label>
                                        <input
                                            className="form-input"
                                            value={message} onChange={e => setMessage(e.target.value)}
                                            placeholder="예: CPU 사용량이 비정상적으로 높습니다!" required
                                        />
                                    </div>
                                    <button type="submit" className="form-submit-btn">
                                        규칙 생성 및 적용
                                    </button>
                                </form>
                            </div>

                            {/* 2. Rule List (Lego Blocks) */}
                            <h3 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '16px' }}>
                                🧩 활성화된 규칙 블럭
                            </h3>
                            <div className="rules-grid">
                                {rules.map(rule => (
                                    <div key={rule.id} className="rule-card">
                                        <div className="rule-header">
                                            <span className="rule-id">#{rule.id}</span>
                                            <button
                                                className="delete-btn"
                                                onClick={() => handleDeleteRule(rule.id)}
                                                title="규칙 삭제"
                                            >
                                                <Trash2 size={16} />
                                            </button>
                                        </div>
                                        <div className="rule-detail"><strong>Table:</strong> {rule.target_table}</div>
                                        <div className="rule-detail"><strong>Col:</strong> {rule.target_column}</div>
                                        <div className="rule-condition">
                                            {rule.operator} {rule.threshold}
                                        </div>
                                        <div className="rule-message">"{rule.message_template}"</div>
                                    </div>
                                ))}
                            </div>
                            {rules.length === 0 && <p style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '20px' }}>등록된 규칙이 없습니다.</p>}
                        </div>
                    )}

                    {activeTab === 'history' && (
                        <div>
                            <table className="dark-table">
                                <thead>
                                    <tr>
                                        <th>Time</th>
                                        <th>Rule ID</th>
                                        <th>Message</th>
                                        <th>Value</th>
                                        <th>Action</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {alerts.map(alert => (
                                        <tr key={alert.id}>
                                            <td style={{ color: 'var(--text-secondary)' }}>
                                                {new Date(alert.created_at).toLocaleString()}
                                            </td>
                                            <td>{alert.rule_id}</td>
                                            <td>{alert.message}</td>
                                            <td style={{ fontWeight: 'bold', color: 'var(--error)' }}>
                                                {alert.value.toFixed(2)}
                                            </td>
                                            <td>
                                                <button
                                                    onClick={() => handleDeleteAlert(alert.id)}
                                                    className="delete-btn"
                                                    title="알림 삭제"
                                                >
                                                    <Trash2 size={16} />
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            {alerts.length === 0 && <p style={{ textAlign: 'center', marginTop: '32px', color: 'var(--text-secondary)' }}>아직 발생한 알림이 없습니다.</p>}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};
