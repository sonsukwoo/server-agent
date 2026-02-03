import React, { useState, useEffect } from 'react';
import { Cpu, HardDrive, Layout } from 'lucide-react';
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

        </div>
    );
};
