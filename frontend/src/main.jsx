import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Theme } from '@radix-ui/themes'
import '@radix-ui/themes/styles.css'
import App from './App.jsx'
import './index.css'

const dark = window.matchMedia('(prefers-color-scheme: dark)').matches

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Theme accentColor="indigo" grayColor="slate" radius="medium" scaling="110%" appearance={dark ? 'dark' : 'light'}>
      <App />
    </Theme>
  </StrictMode>,
)
