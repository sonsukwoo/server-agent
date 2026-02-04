import { useEffect, useState } from 'react'
import { ChatInterface } from './components/Chat/ChatInterface'
import { SettingsModal } from './components/SettingsModal'
import { Bell } from 'lucide-react'
import { ApiClient } from './api/client'
import './index.css'

function App() {
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const apiClient = new ApiClient();

  const refreshUnread = async () => {
    try {
      const alerts = await apiClient.listAlerts();
      const lastSeen = Number(localStorage.getItem('alert_last_seen') || '0');
      const unread = alerts.filter(a => new Date(a.created_at).getTime() > lastSeen).length;
      setUnreadCount(unread);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    refreshUnread();
    const id = setInterval(refreshUnread, 30000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!isSettingsOpen) return;
    const now = Date.now();
    localStorage.setItem('alert_last_seen', String(now));
    setUnreadCount(0);
  }, [isSettingsOpen]);

  return (
    <>
      <ChatInterface />

      {/* Floating Settings Button */}
      <button
        onClick={() => setIsSettingsOpen(true)}
        className="settings-button"
        style={{
          position: 'fixed',
          bottom: '20px',
          left: '20px',
          width: '50px',
          height: '50px',
          borderRadius: '50%',
          backgroundColor: '#374151',
          color: 'white',
          border: 'none',
          boxShadow: '0 4px 6px rgba(0,0,0,0.1)',
          cursor: 'pointer',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          zIndex: 50
        }}
        title="고급 알림 설정"
      >
        <Bell size={24} />
        {unreadCount > 0 && (
          <span className="settings-badge">{unreadCount}</span>
        )}
      </button>

      {/* Settings Modal */}
      <SettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
    </>
  )
}

export default App
