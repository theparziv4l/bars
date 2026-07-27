import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import Layout from './components/layout/Layout'
import { useAuthStore } from './stores'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const ProjectDetail = lazy(() => import('./pages/ProjectDetail'))
const Findings = lazy(() => import('./pages/Findings'))
const HostDashboard = lazy(() => import('./pages/HostDashboard'))
const FlowEditor = lazy(() => import('./pages/FlowEditor'))
const FlowViewer = lazy(() => import('./pages/FlowViewer'))
const Search = lazy(() => import('./pages/Search'))
const Reports = lazy(() => import('./pages/Reports'))
const Library = lazy(() => import('./pages/Library'))
const ChecklistGroupDetail = lazy(() => import('./pages/ChecklistGroupDetail'))
const SyncPeers = lazy(() => import('./pages/SyncPeers'))
const Login = lazy(() => import('./pages/Login'))
const ChangePassword = lazy(() => import('./pages/ChangePassword'))
const UserManagement = lazy(() => import('./pages/UserManagement'))

function LoadingFallback() {
    return (
        <div className="flex items-center justify-center h-64">
            <div className="flex flex-col items-center gap-3">
                <div className="w-8 h-8 border-2 border-accent-primary border-t-transparent rounded-full animate-spin" />
                <span className="text-sm text-dark-400">Loading...</span>
            </div>
        </div>
    )
}

function RequireAuth({ children }) {
    const authRequired = import.meta.env.VITE_AUTH_REQUIRED === 'true'
    const { initialized, isAuthenticated, user } = useAuthStore()
    const location = useLocation()

    if (!authRequired) return children
    if (!initialized) return <LoadingFallback />
    if (!isAuthenticated()) return <Navigate to="/login" replace />

    if (user?.must_change_password && location.pathname !== '/change-password') {
        return <Navigate to="/change-password" replace />
    }

    return children
}

function App() {
    const { initialize } = useAuthStore()

    useEffect(() => {
        initialize()
    }, [initialize])

    return (
        <Router>
            <Toaster
                position="top-right"
                toastOptions={{
                    className: 'glass-card !bg-dark-800 !text-dark-100',
                    duration: 4000,
                }}
            />
            <Routes>
                <Route path="/login" element={<Suspense fallback={<LoadingFallback />}><Login /></Suspense>} />
                <Route path="/change-password" element={
                    <RequireAuth>
                        <Suspense fallback={<LoadingFallback />}><ChangePassword /></Suspense>
                    </RequireAuth>
                } />
                <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
                    <Route index element={<Suspense fallback={<LoadingFallback />}><Dashboard /></Suspense>} />
                    <Route path="project/:projectId" element={<Suspense fallback={<LoadingFallback />}><ProjectDetail /></Suspense>} />
                    <Route path="project/:projectId/findings" element={<Suspense fallback={<LoadingFallback />}><Findings /></Suspense>} />
                    <Route path="host/:hostId" element={<Suspense fallback={<LoadingFallback />}><HostDashboard /></Suspense>} />
                    <Route path="flow/:flowId" element={<Suspense fallback={<LoadingFallback />}><FlowViewer /></Suspense>} />
                    <Route path="library/flow/:flowId" element={<Suspense fallback={<LoadingFallback />}><FlowEditor /></Suspense>} />
                    <Route path="search" element={<Suspense fallback={<LoadingFallback />}><Search /></Suspense>} />
                    <Route path="reports" element={<Suspense fallback={<LoadingFallback />}><Reports /></Suspense>} />
                    <Route path="library" element={<Suspense fallback={<LoadingFallback />}><Library /></Suspense>} />
                    <Route path="library/checklist/:id" element={<Suspense fallback={<LoadingFallback />}><ChecklistGroupDetail /></Suspense>} />
                    <Route path="sync-peers" element={<Suspense fallback={<LoadingFallback />}><SyncPeers /></Suspense>} />
                    <Route path="users" element={<Suspense fallback={<LoadingFallback />}><UserManagement /></Suspense>} />
                </Route>
            </Routes>
        </Router>
    )
}

export default App
