import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
    ArrowLeft, Server, Activity, FolderTree, Terminal,
    Clock, CheckCircle2, XCircle, Loader2, Download, Search,
    File, Folder, ChevronRight, ChevronDown, RefreshCw, HardDrive,
    Lock, Unlock, Eye, EyeOff, Database, Share2, Maximize2, X,
    Settings, Upload, FolderOpen, ArrowUp, AlertCircle, Target,
    Copy, FileText
} from 'lucide-react'
import { hostsApi, filesApi, executionsApi, projectsApi } from '../services/api'
import toast from 'react-hot-toast'
import OutputViewer from '../components/OutputViewer'
import { useWebSocket } from '../hooks/useWebSocket'
import { useExecutionsStore } from '../stores'

export default function HostDashboard() {
    const { hostId } = useParams()
    const [host, setHost] = useState(null)
    const [timeline, setTimeline] = useState([])
    const [fileTree, setFileTree] = useState(null)
    const [shares, setShares] = useState([])
    const [activeTab, setActiveTab] = useState('overview') // overview, timeline, files
    const [loading, setLoading] = useState(true)
    const [refreshingShares, setRefreshingShares] = useState(false)
    const [selectedShare, setSelectedShare] = useState(null)
    const [selectedExecution, setSelectedExecution] = useState(null)
    const [fileSearch, setFileSearch] = useState('')
    const [showOutputModal, setShowOutputModal] = useState(false)
    const [isOutputFullscreen, setIsOutputFullscreen] = useState(false)
    const [showCredentialModal, setShowCredentialModal] = useState(false)
    const [smbCreds, setSmbCreds] = useState({ username: '', password: '', domain: '' })
    const [smbCredsSource, setSmbCredsSource] = useState(null)
    const [savingCreds, setSavingCreds] = useState(false)
    const [downloadingFileId, setDownloadingFileId] = useState(null)
    const [scriptScanLoading, setScriptScanLoading] = useState(false)
    const [viewerFile, setViewerFile] = useState(null)
    const [viewerLoading, setViewerLoading] = useState(false)

    // Subscribe to WebSocket events for real-time execution updates
    useWebSocket()
    const executions = useExecutionsStore((s) => s.executions)
    const prevExecutionsRef = useRef(executions)

    // Refresh timeline + selected execution when any execution status changes
    useEffect(() => {
        // Skip on initial mount
        if (prevExecutionsRef.current === executions) return
        prevExecutionsRef.current = executions

        const refreshTimeline = async () => {
            try {
                const timelineData = await hostsApi.timeline(hostId)
                setTimeline(timelineData.data)
            } catch (_) { /* ignore */ }
        }
        refreshTimeline()

        // Also refresh the selected execution details if one is selected
        if (selectedExecution) {
            loadExecutionDetails(selectedExecution.id)
        }
    }, [executions]) // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        const loadHost = async () => {
            try {
                const { data } = await hostsApi.get(hostId)
                setHost(data)

                const timelineData = await hostsApi.timeline(hostId)
                setTimeline(timelineData.data)

                try {
                    const treeData = await filesApi.getTree(hostId)
                    setFileTree(treeData.data.tree)
                } catch (e) {
                    // Files might not exist
                }

                try {
                    const sharesData = await filesApi.getShares(hostId)
                    setShares(sharesData.data.shares || [])
                } catch (e) {
                    // Shares might not exist
                }
            } catch (error) {
                toast.error('Failed to load host')
            } finally {
                setLoading(false)
            }
        }
        loadHost()
    }, [hostId])

    // Auto-refresh shares/tree when share discovery completes for this host
    useEffect(() => {
        const onSharesDiscovered = async (e) => {
            const data = e.detail
            if (String(data?.host_id) !== String(hostId)) return
            try {
                const sharesData = await filesApi.getShares(hostId)
                setShares(sharesData.data.shares || [])
                const treeData = await filesApi.getTree(hostId)
                setFileTree(treeData.data.tree)
            } catch (_) { /* ignore */ }
        }
        window.addEventListener('shares_discovered', onSharesDiscovered)
        return () => window.removeEventListener('shares_discovered', onSharesDiscovered)
    }, [hostId])

    const loadExecutionDetails = async (execId) => {
        try {
            const { data } = await executionsApi.get(execId)
            setSelectedExecution(data)
        } catch (error) {
            toast.error('Failed to load execution')
        }
    }

    const handleViewFile = async (fileId) => {
        setViewerLoading(true)
        try {
            const { data } = await filesApi.getContent(fileId)
            setViewerFile(data)
        } catch (e) {
            toast.error(e.response?.data?.detail || 'Cannot view file')
        } finally {
            setViewerLoading(false)
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 size={32} className="animate-spin text-accent-primary" />
            </div>
        )
    }

    return (
        <div className="max-w-7xl mx-auto space-y-6">
            {/* Header */}
            <div className="flex items-center gap-4">
                <Link
                    to={`/project/${host?.project_id}`}
                    state={{ activeTab: 'assets' }}
                    className="p-2 text-dark-400 hover:text-dark-200 hover:bg-dark-800 rounded-lg"
                >
                    <ArrowLeft size={20} />
                </Link>
                <div className="flex items-center gap-4">
                    <div className="p-3 rounded-xl bg-accent-info/10">
                        <Server size={24} className="text-accent-info" />
                    </div>
                    <div>
                        <h1 className="text-2xl font-bold text-dark-50">{host?.display_name}</h1>
                        <div className="flex items-center gap-4 text-sm text-dark-400">
                            {host?.ip_address && <span>{host.ip_address}</span>}
                            {host?.os_info && <span>{host.os_info}</span>}
                            <span className={`badge ${host?.status === 'up' ? 'badge-success' : 'badge-warning'}`}>
                                {host?.status}
                            </span>
                            {host?.extra_data?.smb_signing === true && (
                                <span className="flex items-center gap-1 text-accent-success bg-accent-success/10 px-2 py-0.5 rounded text-xs font-medium border border-accent-success/20">
                                    <CheckCircle2 size={12} />
                                    SMB Signing: Enabled
                                </span>
                            )}
                            {host?.extra_data?.smb_signing === false && (
                                <span className="flex items-center gap-1 text-accent-danger bg-accent-danger/10 px-2 py-0.5 rounded text-xs font-medium border border-accent-danger/20">
                                    <AlertCircle size={12} />
                                    SMB Signing: Disabled
                                </span>
                            )}
                        </div>
                    </div>
                </div>
                <button
                    onClick={async () => {
                        if (scriptScanLoading || !host?.services?.length) return
                        setScriptScanLoading(true)
                        try {
                            const { data } = await hostsApi.scriptScan(hostId)
                            toast.success('Script scan started')
                            const timelineData = await hostsApi.timeline(hostId)
                            setTimeline(timelineData.data)
                            setActiveTab('timeline')
                            if (data.execution_id) loadExecutionDetails(data.execution_id)
                        } catch (e) {
                            toast.error(e.response?.data?.detail || 'Failed to start script scan')
                        } finally {
                            setScriptScanLoading(false)
                        }
                    }}
                    disabled={scriptScanLoading || !host?.services?.length}
                    className="btn-primary btn-sm flex items-center gap-2"
                    title={!host?.services?.length ? 'Discover services first' : 'Run NSE script scan for this host'}
                >
                    {scriptScanLoading ? <Loader2 size={16} className="animate-spin" /> : <Target size={16} />}
                    Script Scan
                </button>
            </div>

            {/* Tabs */}
            <div className="flex gap-2 border-b border-dark-800">
                {[
                    { id: 'overview', label: 'Overview', icon: Activity },
                    { id: 'timeline', label: 'Timeline', icon: Clock },
                    { id: 'files', label: 'Files', icon: FolderTree },
                ].map((tab) => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={`flex items-center gap-2 px-4 py-3 border-b-2 transition-colors ${activeTab === tab.id
                            ? 'border-accent-primary text-accent-primary'
                            : 'border-transparent text-dark-400 hover:text-dark-200'
                            }`}
                    >
                        <tab.icon size={18} />
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Overview Tab */}
            {activeTab === 'overview' && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    {/* Services */}
                    <div className="card">
                        <h3 className="font-semibold text-dark-100 mb-4">Services ({host?.services?.length || 0})</h3>
                        {host?.services?.length > 0 ? (
                            <div className="space-y-2">
                                {host.services.map((service) => (
                                    <div key={service.id} className="flex items-center justify-between p-3 bg-dark-800/50 rounded-lg hover:bg-dark-800 transition-colors">
                                        <div className="flex items-start gap-4">
                                            <div className="min-w-[80px]">
                                                <div className="flex items-center gap-2">
                                                    <span className="font-mono text-accent-info font-medium">{service.port}/{service.protocol}</span>
                                                </div>
                                                <span className={`text-xs px-1.5 py-0.5 rounded ${service.state === 'open' ? 'bg-green-500/10 text-green-400' : 'bg-dark-700 text-dark-400'
                                                    }`}>
                                                    {service.state || 'open'}
                                                </span>
                                            </div>
                                            <div>
                                                <div className="text-dark-200 font-medium">{service.name || 'unknown'}</div>
                                                {(service.product || service.version) && (
                                                    <div className="text-sm text-dark-400 mt-0.5">
                                                        {service.product && <span className="text-dark-300 mr-1.5">{service.product}</span>}
                                                        <span>{service.version}</span>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="text-dark-500">No services discovered</p>
                        )}
                    </div>


                </div>
            )}

            {/* Timeline Tab */}
            {activeTab === 'timeline' && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    {/* Timeline List */}
                    <div className="card">
                        <h3 className="font-semibold text-dark-100 mb-4">Execution History</h3>
                        <div className="space-y-3">
                            {timeline.map((exec, idx) => (
                                <div key={exec.id} className="relative">
                                    {idx < timeline.length - 1 && (
                                        <div className="absolute left-[11px] top-8 bottom-0 w-0.5 bg-dark-700"></div>
                                    )}
                                    <button
                                        onClick={() => loadExecutionDetails(exec.id)}
                                        className={`w-full flex items-start gap-4 p-3 rounded-lg transition-colors text-left ${selectedExecution?.id === exec.id ? 'bg-accent-primary/10' : 'hover:bg-dark-800'
                                            }`}
                                    >
                                        <div className={`w-6 h-6 rounded-full flex items-center justify-center ${exec.status === 'completed' ? 'bg-accent-success/20' :
                                            exec.status === 'failed' ? 'bg-accent-danger/20' : 'bg-dark-700'
                                            }`}>
                                            {exec.status === 'completed' && <CheckCircle2 size={14} className="text-accent-success" />}
                                            {exec.status === 'failed' && <XCircle size={14} className="text-accent-danger" />}
                                            {exec.status === 'running' && <Loader2 size={14} className="text-accent-info animate-spin" />}
                                        </div>
                                        <div className="flex-1">
                                            <div className="flex items-center justify-between">
                                                <span className="font-medium text-dark-200">Execution #{timeline.length - idx}</span>
                                                <span className="text-xs text-dark-500">v{exec.version}</span>
                                            </div>
                                            <p className="text-sm text-dark-500">
                                                {exec.started_at ? new Date(exec.started_at).toLocaleString() : 'Pending'}
                                            </p>
                                        </div>
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Execution Details */}
                    <div className="card">
                        <h3 className="font-semibold text-dark-100 mb-4">
                            {selectedExecution ? `Execution #${timeline.findIndex(e => e.id === selectedExecution.id) !== -1 ? timeline.length - timeline.findIndex(e => e.id === selectedExecution.id) : selectedExecution.id}` : 'Select an execution'}
                        </h3>
                        {selectedExecution ? (
                            <div className="space-y-4">
                                <div className="bg-dark-950 rounded-lg p-4 font-mono text-sm">
                                    <div className="text-dark-500 mb-1">Command:</div>
                                    <div className="text-accent-info break-all">{selectedExecution.command}</div>
                                </div>

                                <div>
                                    <div className="flex items-center justify-between mb-2">
                                        <div className="text-sm text-dark-400">Output:</div>
                                        <button
                                            onClick={() => setShowOutputModal(true)}
                                            className="p-1 hover:bg-dark-800 rounded text-dark-400 hover:text-accent-primary transition-colors"
                                            title="Maximize Output"
                                        >
                                            <Maximize2 size={16} />
                                        </button>
                                    </div>
                                    <div className="terminal max-h-64 overflow-auto">
                                        <pre className="whitespace-pre-wrap text-xs">
                                            {selectedExecution.stdout || 'No output'}
                                        </pre>
                                    </div>
                                </div>

                                {selectedExecution.stderr && (
                                    <div>
                                        <div className="text-sm text-accent-danger mb-2">Errors:</div>
                                        <div className="terminal max-h-32 overflow-auto border-accent-danger/30 border">
                                            <pre className="whitespace-pre-wrap text-xs text-accent-danger">
                                                {selectedExecution.stderr}
                                            </pre>
                                        </div>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <p className="text-dark-500">Click an execution to view details</p>
                        )}
                    </div>
                </div>
            )}

            {/* Files Tab */}
            {activeTab === 'files' && (
                <div className="space-y-6">
                    {/* Shares Panel */}
                    <div className="card">
                        <div className="flex items-center justify-between mb-4">
                            <div className="flex items-center gap-2">
                                <Share2 size={18} className="text-accent-primary" />
                                <h3 className="font-semibold text-dark-100">Discovered Shares</h3>
                                <span className="badge">{shares.length}</span>
                            </div>
                            <div className="flex gap-2">
                                <button
                                    onClick={async () => {
                                        setRefreshingShares(true)
                                        try {
                                            const sharesData = await filesApi.getShares(hostId)
                                            setShares(sharesData.data.shares || [])
                                            const treeData = await filesApi.getTree(hostId)
                                            setFileTree(treeData.data.tree)
                                            toast.success('Shares refreshed')
                                        } catch (e) {
                                            toast.error('Failed to refresh shares')
                                        } finally {
                                            setRefreshingShares(false)
                                        }
                                    }}
                                    className="btn-secondary flex items-center gap-2 py-1.5 px-3"
                                    disabled={refreshingShares}
                                >
                                    <RefreshCw size={14} className={refreshingShares ? 'animate-spin' : ''} />
                                    Refresh
                                </button>
                            </div>
                        </div>

                        {shares.length > 0 ? (
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                                {shares.map((share, index) => (
                                    <button
                                        key={share.id || index}
                                        onClick={() => setSelectedShare(selectedShare === share.name ? null : share.name)}
                                        className={`p-4 rounded-lg border transition-all text-left ${selectedShare === share.name
                                            ? 'border-accent-primary bg-accent-primary/10'
                                            : 'border-dark-700 bg-dark-800/50 hover:border-dark-600'
                                            }`}
                                    >
                                        <div className="flex items-center justify-between mb-2">
                                            <div className="flex items-center gap-2">
                                                {share.type === 'nfs' ? (
                                                    <Database size={18} className="text-green-400" />
                                                ) : (
                                                    <HardDrive size={18} className="text-blue-400" />
                                                )}
                                                <span className="font-medium text-dark-100">{share.name}</span>
                                            </div>
                                            <span className={`text-xs px-2 py-0.5 rounded ${share.type === 'nfs' ? 'bg-green-500/20 text-green-400' : 'bg-blue-500/20 text-blue-400'
                                                }`}>
                                                {share.type?.toUpperCase() || 'SMB'}
                                            </span>
                                        </div>
                                        <div className="flex items-center gap-3 text-sm">
                                            {share.is_readable ? (
                                                <span className="flex items-center gap-1 text-accent-success">
                                                    <Eye size={12} /> Read
                                                </span>
                                            ) : (
                                                <span className="flex items-center gap-1 text-dark-500">
                                                    <EyeOff size={12} /> No Read
                                                </span>
                                            )}
                                            {share.is_writable ? (
                                                <span className="flex items-center gap-1 text-accent-warning">
                                                    <Unlock size={12} /> Write
                                                </span>
                                            ) : (
                                                <span className="flex items-center gap-1 text-dark-500">
                                                    <Lock size={12} /> No Write
                                                </span>
                                            )}
                                            {share.file_count > 0 && (
                                                <span className="text-dark-400">
                                                    {share.file_count} files
                                                </span>
                                            )}
                                        </div>
                                        {share.comment && (
                                            <p className="text-xs text-dark-500 mt-2 truncate">{share.comment}</p>
                                        )}
                                    </button>
                                ))}
                            </div>
                        ) : (
                            <div className="text-center py-6 text-dark-500">
                                <Share2 size={32} className="mx-auto mb-2 opacity-50" />
                                <p>No shares discovered yet</p>
                                <p className="text-sm">Run smbmap, netexec, or showmount to discover shares</p>
                            </div>
                        )}
                    </div>

                    {/* File Browser */}
                    <div className="card">
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="font-semibold text-dark-100">
                                {selectedShare ? `Files in ${selectedShare}` : 'All Discovered Files'}
                            </h3>
                            <div className="flex gap-2">
                                {selectedShare && (
                                    <button
                                        onClick={() => setSelectedShare(null)}
                                        className="btn-secondary py-1.5 px-3 text-sm"
                                    >
                                        Show All
                                    </button>
                                )}
                                <button
                                    onClick={async () => {
                                        try {
                                            const { data } = await filesApi.resolveSmbCredentials(hostId)
                                            setSmbCreds({ username: data.username, password: data.password, domain: data.domain })
                                            setSmbCredsSource(data)
                                        } catch (e) {
                                            setSmbCreds({ username: '', password: '', domain: '' })
                                            setSmbCredsSource(null)
                                        }
                                        setShowCredentialModal(true)
                                    }}
                                    className="p-2 hover:bg-dark-800 rounded-lg text-dark-400 hover:text-accent-primary transition-colors"
                                    title="SMB Credentials"
                                >
                                    <Settings size={16} />
                                </button>
                                <div className="relative">
                                    <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-dark-500" />
                                    <input
                                        type="text"
                                        className="input pl-9 py-1.5"
                                        placeholder="Search files..."
                                        value={fileSearch}
                                        onChange={(e) => setFileSearch(e.target.value)}
                                    />
                                </div>
                            </div>
                        </div>

                        {fileTree && Object.keys(fileTree).length > 0 ? (
                            <div className="space-y-1 max-h-96 overflow-y-auto">
                                {Object.entries(fileTree)
                                    .filter(([shareName]) => !selectedShare || shareName === selectedShare)
                                    .map(([shareName, share]) => (
                                        <FileTreeNode
                                            key={shareName}
                                            node={share}
                                            depth={0}
                                            searchTerm={fileSearch}
                                            shareName={shareName}
                                            hostId={hostId}
                                            remotePath=""
                                            onDownload={async (fileId) => {
                                                setDownloadingFileId(fileId)
                                                try {
                                                    await filesApi.fetch(fileId)
                                                    toast.success('File downloaded to project directory')
                                                    const treeData = await filesApi.getTree(hostId)
                                                    setFileTree(treeData.data.tree)
                                                } catch (e) {
                                                    toast.error(e.response?.data?.detail || 'Download failed')
                                                } finally {
                                                    setDownloadingFileId(null)
                                                }
                                            }}
                                            downloadingFileId={downloadingFileId}
                                            onView={handleViewFile}
                                            viewerLoading={viewerLoading}
                                        />
                                    ))}
                            </div>
                        ) : (
                            <div className="text-center py-8">
                                <FolderTree size={48} className="mx-auto text-dark-600 mb-4" />
                                <p className="text-dark-400">No files discovered yet</p>
                                <p className="text-dark-500 text-sm">Run SMB/NFS enumeration to discover files</p>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Output Modal */}
            {showOutputModal && selectedExecution && (
                <div className={`fixed inset-0 z-50 flex items-center justify-center bg-black/80 ${isOutputFullscreen ? 'p-0' : 'p-4'}`}>
                    <div className={`bg-dark-900 w-full shadow-2xl flex flex-col border border-dark-700 ${isOutputFullscreen ? 'h-full max-w-none max-h-none rounded-none' : 'max-w-6xl max-h-[90vh] rounded-lg'}`}>
                        <div className="flex items-center justify-between p-4 border-b border-dark-700">
                            <h3 className="font-semibold text-lg text-dark-100">
                                Execution Output #{selectedExecution.id}
                            </h3>
                            <button
                                onClick={() => { setShowOutputModal(false); setIsOutputFullscreen(false) }}
                                className="p-2 hover:bg-dark-800 rounded-lg text-dark-400 hover:text-white transition-colors"
                            >
                                <X size={20} />
                            </button>
                        </div>
                        <div className="flex-1 min-h-0 flex flex-col bg-dark-950">
                            <div className="flex-1 min-h-0">
                                <OutputViewer
                                    content={selectedExecution.stdout || 'No output'}
                                    command={selectedExecution.command}
                                    className="h-full"
                                    isFullscreen={isOutputFullscreen}
                                    onToggleFullscreen={() => setIsOutputFullscreen(f => !f)}
                                />
                            </div>
                            {selectedExecution.stderr && (
                                <div className="p-4 border-t border-dark-800 max-h-48 overflow-auto">
                                    <div className="text-accent-danger font-bold mb-2">Errors:</div>
                                    <pre className="whitespace-pre-wrap text-sm text-accent-danger">
                                        {selectedExecution.stderr}
                                    </pre>
                                </div>
                            )}
                        </div>
                        {!isOutputFullscreen && (
                            <div className="p-4 border-t border-dark-700 flex justify-end">
                                <button
                                    onClick={() => setShowOutputModal(false)}
                                    className="btn-secondary"
                                >
                                    Close
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Text File Viewer Modal */}
            {viewerFile && (
                <TextFileViewer file={viewerFile} onClose={() => setViewerFile(null)} />
            )}

            {/* SMB Credential Modal */}
            {showCredentialModal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
                    <div className="bg-dark-900 w-full max-w-md rounded-lg shadow-2xl border border-dark-700">
                        <div className="flex items-center justify-between p-4 border-b border-dark-700">
                            <div className="flex items-center gap-2">
                                <Settings size={18} className="text-accent-primary" />
                                <h3 className="font-semibold text-lg text-dark-100">SMB Credentials</h3>
                            </div>
                            <button
                                onClick={() => setShowCredentialModal(false)}
                                className="p-2 hover:bg-dark-800 rounded-lg text-dark-400 hover:text-white transition-colors"
                            >
                                <X size={20} />
                            </button>
                        </div>
                        <div className="p-4 space-y-4">
                            <p className="text-sm text-dark-400">Credentials used for downloading files from and uploading files to SMB shares.</p>
                            {smbCredsSource && smbCredsSource.source === 'auto' && (
                                <div className="flex items-center gap-2 px-3 py-2 rounded bg-accent-success/10 border border-accent-success/20">
                                    <CheckCircle2 size={14} className="text-accent-success shrink-0" />
                                    <span className="text-xs text-accent-success">
                                        Auto-resolved from <strong>{smbCredsSource.domain_name}</strong> domain variables
                                    </span>
                                </div>
                            )}
                            {smbCredsSource && smbCredsSource.source === 'project' && (
                                <div className="flex items-center gap-2 px-3 py-2 rounded bg-accent-info/10 border border-accent-info/20">
                                    <Settings size={14} className="text-accent-info shrink-0" />
                                    <span className="text-xs text-accent-info">Using project-level SMB credentials</span>
                                </div>
                            )}
                            {smbCredsSource && smbCredsSource.source === 'none' && (
                                <div className="flex items-center gap-2 px-3 py-2 rounded bg-accent-warning/10 border border-accent-warning/20">
                                    <AlertCircle size={14} className="text-accent-warning shrink-0" />
                                    <span className="text-xs text-accent-warning">No credentials found. Enter manually below.</span>
                                </div>
                            )}
                            <div>
                                <label className="block text-sm text-dark-300 mb-1">Domain (optional)</label>
                                <input
                                    type="text"
                                    className="input w-full"
                                    placeholder="WORKGROUP"
                                    value={smbCreds.domain}
                                    onChange={(e) => setSmbCreds({ ...smbCreds, domain: e.target.value })}
                                />
                            </div>
                            <div>
                                <label className="block text-sm text-dark-300 mb-1">Username</label>
                                <input
                                    type="text"
                                    className="input w-full"
                                    placeholder="admin"
                                    value={smbCreds.username}
                                    onChange={(e) => setSmbCreds({ ...smbCreds, username: e.target.value })}
                                />
                            </div>
                            <div>
                                <label className="block text-sm text-dark-300 mb-1">Password</label>
                                <input
                                    type="password"
                                    className="input w-full"
                                    placeholder="••••••••"
                                    value={smbCreds.password}
                                    onChange={(e) => setSmbCreds({ ...smbCreds, password: e.target.value })}
                                />
                            </div>
                        </div>
                        <div className="p-4 border-t border-dark-700 flex justify-end gap-2">
                            <button
                                onClick={() => setShowCredentialModal(false)}
                                className="btn-secondary"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={async () => {
                                    setSavingCreds(true)
                                    try {
                                        await projectsApi.saveSmbCredentials(host?.project_id, smbCreds)
                                        toast.success('Credentials saved')
                                        setShowCredentialModal(false)
                                    } catch (e) {
                                        toast.error('Failed to save credentials')
                                    } finally {
                                        setSavingCreds(false)
                                    }
                                }}
                                className="btn-primary flex items-center gap-2"
                                disabled={savingCreds}
                            >
                                {savingCreds && <Loader2 size={14} className="animate-spin" />}
                                Save
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}


const TEXT_VIEWABLE_EXTENSIONS = new Set([
    'txt', 'xml', 'conf', 'ini', 'cfg', 'yaml', 'yml', 'json', 'log', 'md',
    'csv', 'bat', 'ps1', 'sh', 'py', 'html', 'htm', 'css', 'js', 'ts',
    'toml', 'env', 'properties', 'sql', 'rb', 'pl', 'php', 'java', 'c', 'h',
    'cpp', 'hpp', 'cs', 'go', 'rs', 'vbs', 'reg', 'inf', 'pol', 'psm1',
    'psd1', 'cmd', 'jsx', 'tsx',
])

function getFileExtension(name) {
    if (!name || !name.includes('.')) return ''
    return name.rsplit ? name.split('.').pop().toLowerCase() : name.split('.').pop().toLowerCase()
}

function FileTreeNode({ node, depth, searchTerm = '', onDownload, downloadingFileId, shareName, hostId, shareWritable, remotePath = '', onView, viewerLoading }) {
    const [expanded, setExpanded] = useState(depth < 2)
    const isDir = node.type === 'directory' || node.type === 'share' || node.children
    const hasChildren = node.children && Object.keys(node.children).length > 0
    const isFile = !isDir && node.id
    const isWritable = shareWritable || node.is_writable
    const ext = isFile ? getFileExtension(node.name) : ''
    const canView = isFile && node.local_path && TEXT_VIEWABLE_EXTENSIONS.has(ext)

    // Upload state
    const [showPicker, setShowPicker] = useState(false)
    const [browseEntries, setBrowseEntries] = useState([])
    const [browsePath, setBrowsePath] = useState('/home/kali')
    const [browseParent, setBrowseParent] = useState(null)
    const [browseLoading, setBrowseLoading] = useState(false)
    const [uploading, setUploading] = useState(false)

    const loadDir = async (path) => {
        setBrowseLoading(true)
        try {
            const { data } = await filesApi.browseLocal(path)
            setBrowseEntries(data.entries || [])
            setBrowsePath(data.current_path)
            setBrowseParent(data.parent_path)
        } catch (e) {
            toast.error('Cannot access directory')
        } finally {
            setBrowseLoading(false)
        }
    }

    const openPicker = async (e) => {
        e.stopPropagation()
        setShowPicker(true)
        await loadDir(browsePath)
    }

    const handleUploadFile = async (entry) => {
        setUploading(true)
        try {
            // Determine which share/path to upload to
            await filesApi.upload({
                host_id: Number(hostId),
                share_name: shareName,
                local_file_path: entry.path,
                remote_path: remotePath,
            })
            toast.success(`Uploaded ${entry.name} to //${shareName}`)
            setShowPicker(false)
        } catch (e) {
            toast.error(e.response?.data?.detail || 'Upload failed')
        } finally {
            setUploading(false)
        }
    }

    // Filter logic for search
    const matchesSearch = !searchTerm ||
        node.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (node.interest_reason && node.interest_reason.toLowerCase().includes(searchTerm.toLowerCase()))

    const hasMatchingChildren = hasChildren && Object.values(node.children).some(child => {
        const childMatches = child.name.toLowerCase().includes(searchTerm.toLowerCase())
        const childrenMatch = child.children && Object.values(child.children).some(
            gc => gc.name.toLowerCase().includes(searchTerm.toLowerCase())
        )
        return childMatches || childrenMatch
    })

    if (searchTerm && !matchesSearch && !hasMatchingChildren) {
        return null
    }

    return (
        <div style={{ marginLeft: depth * 16 }}>
            <div
                className={`tree-node ${node.is_interesting ? 'bg-accent-warning/10' : ''} ${searchTerm && matchesSearch ? 'bg-accent-primary/5' : ''
                    }`}
                onClick={() => isDir && setExpanded(!expanded)}
            >
                {isDir ? (
                    <>
                        {hasChildren && (expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />)}
                        <Folder size={16} className={node.type === 'share' ? 'text-blue-400' : 'text-accent-warning'} />
                    </>
                ) : (
                    <>
                        <span className="w-3.5"></span>
                        <File size={16} className="text-dark-400" />
                    </>
                )}
                <span className={`text-sm ${node.is_interesting ? 'text-accent-warning font-medium' : 'text-dark-300'}`}>
                    {node.name}
                </span>
                {node.last_modified && <span className="text-xs text-dark-600 ml-auto">{node.last_modified}</span>}
                {node.size != null && <span className="text-xs text-dark-500 ml-2">{formatSize(node.size)}</span>}
                {node.is_interesting && (
                    <span className="badge-warning text-xs ml-2">{node.interest_reason}</span>
                )}
                {canView && onView && (
                    <button
                        onClick={(e) => { e.stopPropagation(); onView(node.id) }}
                        className="ml-2 p-1 hover:bg-dark-700 rounded text-dark-500 hover:text-accent-success transition-colors"
                        title="View file content"
                        disabled={viewerLoading}
                    >
                        <Eye size={14} />
                    </button>
                )}
                {isFile && onDownload && (
                    <button
                        onClick={(e) => { e.stopPropagation(); onDownload(node.id) }}
                        className="ml-2 p-1 hover:bg-dark-700 rounded text-dark-500 hover:text-accent-info transition-colors"
                        title="Download from share"
                        disabled={downloadingFileId === node.id}
                    >
                        {downloadingFileId === node.id ? (
                            <Loader2 size={14} className="animate-spin" />
                        ) : (
                            <Download size={14} />
                        )}
                    </button>
                )}
                {isDir && isWritable && (
                    <button
                        onClick={openPicker}
                        className="ml-2 p-1 hover:bg-dark-700 rounded text-dark-500 hover:text-accent-warning transition-colors"
                        title="Upload file to this directory"
                        disabled={uploading}
                    >
                        {uploading ? (
                            <Loader2 size={14} className="animate-spin" />
                        ) : (
                            <Upload size={14} />
                        )}
                    </button>
                )}
            </div>

            {expanded && hasChildren && (
                <div>
                    {Object.entries(node.children).map(([childName, child]) => (
                        <FileTreeNode
                            key={childName}
                            node={child}
                            depth={depth + 1}
                            searchTerm={searchTerm}
                            onDownload={onDownload}
                            downloadingFileId={downloadingFileId}
                            shareName={shareName}
                            hostId={hostId}
                            shareWritable={isWritable}
                            remotePath={remotePath ? `${remotePath}/${childName}` : childName}
                            onView={onView}
                            viewerLoading={viewerLoading}
                        />
                    ))}
                </div>
            )}

            {/* File Picker Modal */}
            {showPicker && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={() => setShowPicker(false)}>
                    <div className="bg-dark-900 w-full max-w-xl rounded-lg shadow-2xl border border-dark-700 flex flex-col max-h-[70vh]" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-between p-3 border-b border-dark-700">
                            <div className="flex items-center gap-2">
                                <FolderOpen size={18} className="text-accent-primary" />
                                <h3 className="font-semibold text-dark-100">Select File to Upload</h3>
                                <span className="text-xs text-dark-500">→ {shareName}/{node.name !== shareName ? node.name : ''}</span>
                            </div>
                            <button
                                onClick={() => setShowPicker(false)}
                                className="p-1.5 hover:bg-dark-800 rounded-lg text-dark-400 hover:text-white transition-colors"
                            >
                                <X size={18} />
                            </button>
                        </div>

                        {/* Path bar */}
                        <div className="px-3 py-2 border-b border-dark-800 flex items-center gap-2">
                            {browseParent && (
                                <button
                                    onClick={() => loadDir(browseParent)}
                                    className="p-1.5 hover:bg-dark-800 rounded text-dark-400 hover:text-white transition-colors"
                                    title="Go up"
                                >
                                    <ArrowUp size={16} />
                                </button>
                            )}
                            <code className="text-xs text-dark-400 truncate flex-1">{browsePath}</code>
                        </div>

                        {/* File list */}
                        <div className="flex-1 overflow-y-auto p-1">
                            {browseLoading ? (
                                <div className="flex items-center justify-center py-8">
                                    <Loader2 size={24} className="animate-spin text-dark-500" />
                                </div>
                            ) : browseEntries.length === 0 ? (
                                <div className="text-center py-8 text-dark-500 text-sm">Empty directory</div>
                            ) : (
                                browseEntries.map((entry) => (
                                    <button
                                        key={entry.path}
                                        onClick={() => entry.is_dir ? loadDir(entry.path) : handleUploadFile(entry)}
                                        className="w-full flex items-center gap-2 px-3 py-1.5 rounded hover:bg-dark-800 transition-colors text-left group"
                                        disabled={!entry.is_dir && uploading}
                                    >
                                        {entry.is_dir ? (
                                            <Folder size={16} className="text-accent-warning shrink-0" />
                                        ) : (
                                            <File size={16} className="text-dark-400 shrink-0" />
                                        )}
                                        <span className="text-sm text-dark-200 truncate flex-1">{entry.name}</span>
                                        {!entry.is_dir && entry.size != null && (
                                            <span className="text-xs text-dark-600">{formatSize(entry.size)}</span>
                                        )}
                                        {entry.is_dir ? (
                                            <ChevronRight size={14} className="text-dark-600 opacity-0 group-hover:opacity-100" />
                                        ) : (
                                            <Upload size={14} className="text-dark-600 opacity-0 group-hover:opacity-100 text-accent-warning" />
                                        )}
                                    </button>
                                ))
                            )}
                        </div>

                        <div className="p-3 border-t border-dark-700 flex justify-end">
                            <button onClick={() => setShowPicker(false)} className="btn-secondary text-sm">
                                Cancel
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

function TextFileViewer({ file, onClose }) {
    const [searchTerm, setSearchTerm] = useState('')
    const [copied, setCopied] = useState(false)
    const lines = (file.content || '').split('\n')

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(file.content)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        } catch {
            toast.error('Failed to copy')
        }
    }

    const filteredLines = searchTerm
        ? lines.map((line, i) => ({ line, num: i + 1, match: line.toLowerCase().includes(searchTerm.toLowerCase()) }))
        : lines.map((line, i) => ({ line, num: i + 1, match: false }))

    const visibleLines = searchTerm ? filteredLines.filter(l => l.match) : filteredLines

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={onClose}>
            <div
                className="bg-dark-900 w-full max-w-4xl rounded-lg shadow-2xl border border-dark-700 flex flex-col"
                style={{ maxHeight: '85vh' }}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between p-3 border-b border-dark-700">
                    <div className="flex items-center gap-2">
                        <FileText size={18} className="text-accent-primary" />
                        <h3 className="font-semibold text-dark-100">{file.filename}</h3>
                        {file.extension && (
                            <span className="text-xs bg-dark-700 px-1.5 py-0.5 rounded text-dark-400">.{file.extension}</span>
                        )}
                        {file.truncated && (
                            <span className="text-xs bg-accent-warning/15 text-accent-warning px-1.5 py-0.5 rounded">Truncated (1 MB limit)</span>
                        )}
                        <span className="text-xs text-dark-500">{lines.length} lines</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <div className="relative">
                            <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-dark-500" />
                            <input
                                type="text"
                                className="input-field bg-dark-800 border-dark-700 text-sm pl-7 w-48"
                                placeholder="Search..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                            />
                        </div>
                        <button
                            onClick={handleCopy}
                            className="p-1.5 hover:bg-dark-800 rounded text-dark-400 hover:text-white transition-colors"
                            title="Copy content"
                        >
                            {copied ? <CheckCircle2 size={16} className="text-accent-success" /> : <Copy size={16} />}
                        </button>
                        <button
                            onClick={onClose}
                            className="p-1.5 hover:bg-dark-800 rounded text-dark-400 hover:text-white transition-colors"
                        >
                            <X size={18} />
                        </button>
                    </div>
                </div>
                <div className="flex-1 overflow-auto font-mono text-sm">
                    <table className="w-full">
                        <tbody>
                            {visibleLines.map((entry) => (
                                <tr key={entry.num} className={`${entry.match && searchTerm ? 'bg-accent-warning/10' : 'hover:bg-dark-800/50'}`}>
                                    <td className="text-right text-dark-600 select-none px-3 py-0 border-r border-dark-800 w-12 text-xs sticky left-0 bg-dark-900">
                                        {entry.num}
                                    </td>
                                    <td className="px-3 py-0 whitespace-pre text-dark-200">
                                        {entry.line || '\u00A0'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    )
}

function formatSize(bytes) {
    if (!bytes) return ''
    const units = ['B', 'KB', 'MB', 'GB']
    let size = bytes
    let unitIndex = 0
    while (size >= 1024 && unitIndex < units.length - 1) {
        size /= 1024
        unitIndex++
    }
    return `${size.toFixed(1)} ${units[unitIndex]}`
}
