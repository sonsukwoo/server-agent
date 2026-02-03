import React, { useState } from 'react';

interface DataTableProps {
    data: any[];
}

export const DataTable: React.FC<DataTableProps> = ({ data }) => {
    const [showAll, setShowAll] = useState(false);

    if (!data || data.length === 0) {
        return <div className="p-4 text-secondary">결과 데이터가 없습니다.</div>;
    }

    const columns = Object.keys(data[0]);
    const rows = showAll ? data : data.slice(0, 10);
    const canExpand = data.length > 10;

    return (
        <div className="data-table-container">
            <table className="data-table">
                <thead>
                    <tr>
                        {columns.map((col) => (
                            <th key={col}>{col}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, i) => (
                        <tr key={i}>
                            {columns.map((col) => (
                                <td key={col}>{String(row[col])}</td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
            {canExpand && (
                <div className="data-table-footer">
                    <button
                        className="data-table-toggle"
                        onClick={() => setShowAll(prev => !prev)}
                        type="button"
                    >
                        {showAll ? '접기' : `더 보기 (+${data.length - 10})`}
                    </button>
                </div>
            )}
        </div>
    );
};
