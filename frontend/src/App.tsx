import { useState } from 'react'
import { ChatInterface } from './components/Chat/ChatInterface'
import { SettingsModal } from './components/SettingsModal'
import './index.css'

function App() {
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  return (
    <>
      <ChatInterface />

      {/* Floating Settings Button */}
      <button
        onClick={() => setIsSettingsOpen(true)}
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
          fontSize: '24px',
          cursor: 'pointer',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          zIndex: 50
        }}
        title="고급 알림 설정"
      >
        ⚙️
      </button>

      {/* Settings Modal */}
      <SettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
    </>
  )
}

export default App
