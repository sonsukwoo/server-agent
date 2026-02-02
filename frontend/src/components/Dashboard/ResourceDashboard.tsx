import React, { useState, useEffect } from 'react';
import { Cpu, HardDrive, Layout, Activity, ArrowDown, ArrowUp } from 'lucide-react';
import { ApiClient } from '../../api/client';

export const ResourceDashboard: React.FC = () => {
    const [summary, setSummary] = useState<any>(null);

    const fetchSummary = async () => {
        try {
            const data = await ApiClient.getResourceSummary();
            setSummary(data);
        } catch (error) {
            console.error('Failed to fetch resource summary:', error);
        }
    };

    useEffect(() => {
        fetchSummary();
        const interval = setInterval(fetchSummary, 10000);
        return () => clearInterval(interval);
    }, []);

    if (!summary) return null;

    return (
        <div className="dashboard-container">
            <div className="dashboard-card">
                <div className="card-icon cpu">
                    <Cpu size={18} />
                </div>
                <div className="card-info">
                    <span className="card-label">CPU 전체</span>
                    <span className="card-value">{summary["CPU 전체"] || '0%'}</span>
                </div>
            </div>

            <div className="dashboard-card">
                <div className="card-icon ram">
                    <Layout size={18} />
                </div>
                <div className="card-info">
                    <span className="card-label">RAM 사용률</span>
                    <span className="card-value">{summary["RAM 사용률"] || '0%'}</span>
                </div>
            </div>

            <div className="dashboard-card">
                <div className="card-icon disk">
                    <HardDrive size={18} />
                </div>
                <div className="card-info">
                    <span className="card-label">디스크 사용률</span>
                    <span className="card-value">{summary["디스크 사용률"] || '0%'}</span>
                </div>
            </div>

            <div className="dashboard-card network">
                <div className="card-icon net">
                    <Activity size={18} />
                </div>
                <div className="card-info">
                    <span className="card-label">네트워크 (RX / TX)</span>
                    <div className="net-values">
                        <span className="net-item">
                            <ArrowDown size={12} /> {summary["네트워크 수신"] || '0MB/s'}
                        </span>
                        <span className="net-item">
                            <ArrowUp size={12} /> {summary["네트워크 송신"] || '0MB/s'}
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
};
