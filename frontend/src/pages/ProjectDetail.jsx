import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useParams, Link, useLocation } from 'react-router-dom'
import {
    ArrowLeft, Plus, Play, ChevronDown, ChevronRight, Server,
    CheckCircle2, XCircle, Clock, Loader2, Terminal, FileText,
    Target, Settings2, Workflow, List, Grid3X3, StopCircle, AlertCircle, Trash2, Search, Download, ChevronLeft,
    ArrowUpDown, ArrowUp, ArrowDown, RefreshCw, Filter, X, Ban, ShieldCheck, FolderDown
} from 'lucide-react'
import { projectsApi, checklistsApi, hostsApi, executionsApi, flowsApi, syncPeersApi, adDomainsApi } from '../services/api'
import { useAuthStore, useChecklistsStore, useHostsStore, useAppStore, useExecutionsStore } from '../stores'
import ProjectVariables from '../components/ProjectVariables'
import toast from 'react-hot-toast'
import OutputViewer from '../components/OutputViewer'

export default function ProjectDetail() {
    const { projectId } = useParams()
    const location = useLocation()
    const [project, setProject] = useState(null)
    const [loading, setLoading] = useState(true)
    const [activeView, setActiveView] = useState(location.state?.activeTab || 'checklist') // 'checklist' or 'assets'
    const [selectedItem, setSelectedItem] = useState(null)
    const [showExecutionPanel, setShowExecutionPanel] = useState(false)
    const [showImportModal, setShowImportModal] = useState(false)
    const [importType, setImportType] = useState('checklist') // 'checklist' or 'flow'
    const [executionVariables, setExecutionVariables] = useState({})
    const [targetSearch, setTargetSearch] = useState('')
    const [executeDomainFilter, setExecuteDomainFilter] = useState('')
    const [isEditingCommand, setIsEditingCommand] = useState(false)
    const [tempCommand, setTempCommand] = useState('')


    const [outputModalState, setOutputModalState] = useState({ open: false, executionId: null, itemId: null, title: '' })
    const [selectedItems, setSelectedItems] = useState([]) // Array of item IDs

    // Execution state tracking for the modal
    const [executionState, setExecutionState] = useState({
        status: 'idle', // 'idle' | 'running' | 'completed' | 'failed' | 'cancelled'
        executionId: null,
        output: ''
    })


    const [deleteModalState, setDeleteModalState] = useState({
        open: false,
        type: null, // 'group' or 'item'
        id: null,
        name: ''
    })

    const [scrollToGroupId, setScrollToGroupId] = useState(null)
    const scrollToGroupRef = useRef(null)
    const [executionModalVariablesOpen, setExecutionModalVariablesOpen] = useState(false)
    const [members, setMembers] = useState([])
    const [memberInput, setMemberInput] = useState('')
    const [syncingProject, setSyncingProject] = useState(false)
    const [executionPanelHosts, setExecutionPanelHosts] = useState(null)
    const [executionPanelHostsLoading, setExecutionPanelHostsLoading] = useState(false)
    const [assetsSearch, setAssetsSearch] = useState('')
    const assetsSearchDebounceRef = useRef(null)
    const portSearchDebounceRef = useRef(null)
    const [hostFilterOptions, setHostFilterOptions] = useState({ os_values: [], smb_signing_values: [], domain_values: [] })
    useEffect(() => () => {
        if (assetsSearchDebounceRef.current) clearTimeout(assetsSearchDebounceRef.current)
        if (portSearchDebounceRef.current) clearTimeout(portSearchDebounceRef.current)
    }, [])

    const { groups, fetchGroups, createGroup, createItem, updateItemStatus, deleteGroup, deleteItem, batchProgress } = useChecklistsStore()
    const { hosts, hostsTotal, hostsPage, hostsPerPage, hostsSortBy, hostsSortOrder, hostsTagsFilter, hostsSmbSigningFilter, hostsOsFilter, hostsPortSearch, hostsDomainFilter, hostsScopeFilter, setHostsSort, setHostsTagsFilter, setHostsSmbSigningFilter, setHostsOsFilter, setHostsPortSearch, setHostsDomainFilter, setHostsScopeFilter, fetchHosts, createHost } = useHostsStore()
    const { setCurrentProject } = useAppStore()
    // Excluded hosts are out of scope: keep them out of every target picker. The
    // backend refuses them too — this just stops the operator from picking a host
    // whose run would only come back failed.
    const listForTarget = (executionPanelHosts != null ? executionPanelHosts : hosts).filter(h => !h.excluded)
    const { startExecution, executions } = useExecutionsStore()
    const { user } = useAuthStore()

    const fetchMembers = useCallback(async (id) => {
        if (!user) return
        try {
            const { data } = await projectsApi.listMembers(id)
            setMembers(data)
        } catch (error) {
            // Ignore when auth is disabled or user is not project member yet
        }
    }, [user])

    // Connect to WebSocket for real-time execution updates (including flow executions)
    // Socket is mounted once by Layout — a second mount here opened a second connection
    // and ran every store reducer twice (duplicate toasts, doubled store churn).

    // Sync WebSocket execution updates with modal state
    useEffect(() => {
        if (!executionState.executionId) return

        // Find the current execution in the store (updated via WebSocket)
        const currentExec = executions.find(e => e.id === executionState.executionId)
        if (!currentExec) return

        // Update modal state if status changed
        const status = currentExec.status
        if (['completed', 'failed', 'cancelled', 'timeout'].includes(status)) {
            setExecutionState(prev => ({ ...prev, status }))
        } else if (status === 'running' && executionState.status !== 'running') {
            setExecutionState(prev => ({ ...prev, status: 'running' }))
        }
    }, [executions, executionState.executionId])

    const [projectFlows, setProjectFlows] = useState([])
    const [projectHostTags, setProjectHostTags] = useState([])

    const fetchFlows = useCallback(async (id) => {
        try {
            const { data } = await flowsApi.list(id)
            setProjectFlows(data)
        } catch (error) {
            console.error('Failed to fetch flows', error)
        }
    }, [])

    // Server-computed project data: overall progress, host tag list, filter option sets.
    // Discovery and executions change all three, so this is re-run on the same websocket
    // events instead of only at mount (it used to move only after a page reload).
    const refreshProjectSummary = useCallback(async () => {
        if (!projectId) return
        await Promise.all([
            projectsApi.get(projectId).then(({ data }) => {
                setProject(data)
                setCurrentProject(data)
            }).catch(() => { }),
            projectsApi.getHostTags(projectId).then(({ data }) => {
                setProjectHostTags(data.tags || [])
            }).catch(() => setProjectHostTags([])),
            projectsApi.getHostFilterOptions(projectId).then(({ data }) => {
                setHostFilterOptions(data || { os_values: [], smb_signing_values: [], domain_values: [] })
            }).catch(() => setHostFilterOptions({ os_values: [], smb_signing_values: [], domain_values: [] })),
        ])
    }, [projectId, setCurrentProject])

    useEffect(() => {
        const loadProject = async () => {
            try {
                await Promise.all([
                    refreshProjectSummary(),
                    fetchGroups(projectId),
                    fetchHosts(projectId, { per_page: 100, page: 1 }),
                    fetchFlows(projectId),
                ])
                await fetchMembers(projectId)
            } catch (error) {
                toast.error('Failed to load project')
            } finally {
                setLoading(false)
            }
        }
        loadProject()
    }, [projectId, fetchGroups, fetchHosts, fetchFlows, fetchMembers, refreshProjectSummary])

    useEffect(() => {
        const onProjectDataChanged = (e) => {
            const pid = e.detail?.project_id
            if (pid != null && String(pid) !== String(projectId)) return
            refreshProjectSummary()
        }
        window.addEventListener('host_changed', onProjectDataChanged)
        window.addEventListener('execution_finished', onProjectDataChanged)
        return () => {
            window.removeEventListener('host_changed', onProjectDataChanged)
            window.removeEventListener('execution_finished', onProjectDataChanged)
        }
    }, [refreshProjectSummary, projectId])

    // Scroll to Nmap Script Scan group after generating script scans
    useEffect(() => {
        if (scrollToGroupId && scrollToGroupRef.current && groups.some(g => g.id === scrollToGroupId)) {
            scrollToGroupRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
            setScrollToGroupId(null)
        }
    }, [scrollToGroupId, groups])

    // When Execute Task modal opens, fetch all project hosts for target selection (do not touch store)
    useEffect(() => {
        if (!showExecutionPanel || !selectedItem || !projectId) return
        let cancelled = false
        setExecutionPanelHostsLoading(true)
        ;(async () => {
            try {
                let allHosts = []
                let page = 1
                const perPage = 5000
                while (true) {
                    const { data } = await hostsApi.list(projectId, { per_page: perPage, page })
                    allHosts = allHosts.concat(data.items || [])
                    const total = data.total ?? 0
                    if ((data.items || []).length === 0 || allHosts.length >= total) break
                    page++
                }
                if (!cancelled) setExecutionPanelHosts(allHosts)
            } catch (err) {
                if (!cancelled) toast.error('Failed to load hosts for target selection')
            } finally {
                if (!cancelled) setExecutionPanelHostsLoading(false)
            }
        })()
        return () => { cancelled = true }
    }, [showExecutionPanel, selectedItem, projectId])

    const handleCreateHost = async () => {
        const ip = prompt('Enter Host IP (e.g. 192.168.1.10) or Hostname:')
        if (!ip) return

        try {
            await createHost({
                project_id: projectId,
                ip_address: ip,
                display_name: ip, // improved default name
                os_info: 'Unknown'
            })
            toast.success('Host added')
            fetchHosts(projectId, { per_page: 100, page: 1 })
        } catch (error) {
            toast.error('Failed to add host')
        }
    }
    const handleCreateFlow = async () => {
        const name = prompt('Enter flow name:')
        if (!name) return

        try {
            await flowsApi.create({
                project_id: projectId,
                name,
                description: 'Project flow',
                is_template: false,
                steps: []
            })
            toast.success('Flow created')
            fetchFlows(projectId)
        } catch (error) {
            toast.error('Failed to create flow')
        }
    }





    const handleExecuteItem = async (item, hostId = null, variables = {}, commandOverride = null, forceTakeover = false) => {
        const claim = item.claim
        const claimMatchesScope = (claim?.host_id ?? null) === (hostId ?? null)
        const claimedByOther = claim?.is_active && claimMatchesScope && claim?.claimed_by_user_id && claim.claimed_by_user_id !== user?.id

        if (claimedByOther && !forceTakeover) {
            const approved = confirm(`This item is currently claimed by ${claim.claimed_by_username || 'another operator'}. Force takeover and run?`)
            if (!approved) {
                return
            }
            return handleExecuteItem(item, hostId, variables, commandOverride, true)
        }

        try {
            const execution = await startExecution({
                item_id: item.id,
                host_id: hostId,
                variables: variables || executionVariables,
                command_override: commandOverride,
                force_takeover: forceTakeover,
            })

            updateItemStatus(item.id, 'running', execution.id)

            toast.success(`Started: ${item.name}`)
            handleCloseExecutionPanel()
            setExecutionState({ executionId: execution.id, status: 'running', output: '' })

            // Clear selection if executed from bulk or single
            if (selectedItems.includes(item.id)) {
                setSelectedItems(selectedItems.filter(i => i !== item.id))
            }
        } catch (error) {
            console.error(error)
            const detail = error?.response?.data?.detail
            if (error?.response?.status === 409 && detail?.reason === 'claimed_by_other' && !forceTakeover) {
                const approved = confirm(`Claim conflict with ${detail.claimed_by_username || 'another operator'}. Force takeover and run?`)
                if (approved) {
                    return handleExecuteItem(item, hostId, variables, commandOverride, true)
                }
            }
            toast.error('Failed to start execution')
        }
    }

    const handleStopExecution = async (executionId, isBatch = false) => {
        if (!executionId) return
        try {
            if (isBatch) {
                await executionsApi.cancelBatch(executionId)
                toast.success('Batch execution killed')
            } else {
                await executionsApi.cancel(executionId)
                toast.success('Execution stopped')
            }
        } catch (error) {
            toast.error('Failed to stop execution')
        }
    }

    // Single reset path for the Execute panel. Every close (Cancel, Escape, successful
    // start) must run this: leftover executionPanelHosts made the modal show the previous
    // host list — hiding hosts discovered since — and a leftover targetSearch /
    // executeDomainFilter silently shrinks what "Select All" picks.
    const handleCloseExecutionPanel = useCallback(() => {
        setShowExecutionPanel(false)
        setSelectedItem(null)
        setExecutionPanelHosts(null)
        setExecutionPanelHostsLoading(false)
        setExecuteDomainFilter('')
        setTargetSearch('')
        setExecutionState({ status: 'idle', executionId: null, output: '' })
        setIsEditingCommand(false)
        setTempCommand('')
    }, [])

    const handleBulkExecute = useCallback(async () => {
        if (selectedItems.length === 0) return
        try {
            await executionsApi.startBulk({
                item_ids: selectedItems,
                host_id: null,
                variables: {},
                sequential: false
            })
            toast.success(`Started ${selectedItems.length} tasks`)
            setSelectedItems([])
        } catch (error) {
            toast.error('Failed to start bulk execution')
        }
    }, [selectedItems])

    const getStatusIcon = useCallback((status) => {
        switch (status) {
            case 'completed': return <CheckCircle2 size={16} className="text-accent-success" />
            case 'running': return <Loader2 size={16} className="text-accent-info animate-spin" />
            case 'failed': return <XCircle size={16} className="text-accent-danger" />
            case 'pending': return <Clock size={16} className="text-dark-500" />
            default: return <Clock size={16} className="text-dark-500" />
        }
    }, [])

    const handleDeleteFlow = async (id) => {
        if (!confirm('Are you sure you want to delete this flow?')) return

        try {
            await flowsApi.delete(id)
            toast.success('Flow deleted')
            fetchFlows(projectId)
        } catch (error) {
            toast.error('Failed to delete flow')
        }
    }

    const handleDeleteHost = async (id) => {
        if (!confirm('Are you sure you want to delete this host?')) return

        try {
            await hostsApi.delete(id)
            toast.success('Host deleted')
            fetchHosts(projectId, { per_page: 100, page: hostsPage })
        } catch (error) {
            toast.error('Failed to delete host')
        }
    }

    const handleDeleteClick = (type, id, name) => {
        setDeleteModalState({
            open: true,
            type,
            id,
            name
        })
    }

    const handleConfirmDelete = async (softDelete) => {
        try {
            if (deleteModalState.type === 'group') {
                await deleteGroup(deleteModalState.id, softDelete)
                toast.success('Group deleted')
            } else {
                await deleteItem(deleteModalState.id, softDelete)
                toast.success('Item deleted')
            }
            setDeleteModalState({ open: false, type: null, id: null, name: '' })
        } catch (error) {
            toast.error('Failed to delete')
        }
    }

    const handleAddMember = async () => {
        if (!memberInput.trim()) return
        try {
            await projectsApi.addMember(projectId, { username_or_email: memberInput.trim() })
            setMemberInput('')
            await fetchMembers(projectId)
            toast.success('Member added')
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to add member')
        }
    }

    const handleSyncProject = async () => {
        setSyncingProject(true)
        try {
            await syncPeersApi.runSync(Number(projectId))
            toast.success('Project synced with peers')
            fetchGroups(projectId)
            fetchHosts(projectId, { per_page: 100, page: hostsPage })
            fetchFlows(projectId)
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Sync failed'
            toast.error(typeof msg === 'string' ? msg : 'Sync failed')
        } finally {
            setSyncingProject(false)
        }
    }

    const handleRemoveMember = async (member) => {
        if (!confirm(`Remove ${member.username} from this project?`)) return
        try {
            await projectsApi.removeMember(projectId, member.user_id)
            await fetchMembers(projectId)
            toast.success('Member removed')
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to remove member')
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
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <Link to="/" className="p-2 text-dark-400 hover:text-dark-200 hover:bg-dark-800 rounded-lg">
                        <ArrowLeft size={20} />
                    </Link>
                    <div>
                        <h1 className="text-2xl font-bold text-dark-50">{project?.name}</h1>
                        <p className="text-dark-400">{project?.description}</p>
                    </div>
                </div>

                {/* Sync project + View Toggle */}
                <div className="flex items-center gap-3">
                    <button
                        type="button"
                        onClick={handleSyncProject}
                        disabled={syncingProject}
                        className="btn-secondary inline-flex items-center gap-2 px-3 py-2 rounded-lg"
                        title="Sync this project with all peers"
                    >
                        {syncingProject ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw size={18} />}
                        {syncingProject ? 'Syncing…' : 'Sync project'}
                    </button>
                    <Link
                        to={`/project/${projectId}/findings`}
                        className="btn-secondary inline-flex items-center gap-2 px-3 py-2 rounded-lg"
                        title="View findings for this project"
                    >
                        Findings
                    </Link>
                    <div className="flex items-center gap-2 bg-dark-800 rounded-lg p-1">
                    <button
                        onClick={() => setActiveView('checklist')}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all ${activeView === 'checklist'
                            ? 'bg-accent-primary text-white'
                            : 'text-dark-400 hover:text-dark-200'
                            }`}
                    >
                        <List size={18} />
                        Checklist
                    </button>
                    <button
                        onClick={() => { setActiveView('flows'); fetchFlows(projectId); }}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all ${activeView === 'flows'
                            ? 'bg-accent-primary text-white'
                            : 'text-dark-400 hover:text-dark-200'
                            }`}
                    >
                        <Workflow size={18} />
                        Flows
                    </button>
                    <button
                        onClick={() => setActiveView('assets')}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all ${activeView === 'assets'
                            ? 'bg-accent-primary text-white'
                            : 'text-dark-400 hover:text-dark-200'
                            }`}
                    >
                        <Grid3X3 size={18} />
                        Assets
                    </button>
                    <button
                        onClick={() => setActiveView('variables')}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all ${activeView === 'variables'
                            ? 'bg-accent-primary text-white'
                            : 'text-dark-400 hover:text-dark-200'
                            }`}
                    >
                        <Settings2 size={18} />
                        Variables
                    </button>
                    </div>
                </div>
            </div>

            {/* Checklist View */}
            {activeView === 'checklist' && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* Checklist Groups */}
                    <div className="lg:col-span-2 space-y-4">
                        {/* Persistent Toolbar */}
                        <div className="flex items-center justify-between">
                            <h2 className="text-lg font-semibold text-dark-100">Checklists</h2>
                        </div>
                        <div className="flex gap-2">
                            <button
                                onClick={async () => {
                                    try {
                                        setActiveView('checklist')
                                        const { data } = await projectsApi.generateScriptScan(projectId)
                                        await fetchGroups(projectId)
                                        if (data.group_id) setScrollToGroupId(data.group_id)
                                        toast.success(`Created ${data.items?.length || 0} Service Scan items${data.skipped_services?.length ? ` (${data.skipped_services.length} services skipped)` : ''}`)
                                    } catch (e) {
                                        toast.error(e.response?.data?.detail || 'Failed to generate service scans')
                                    }
                                }}
                                className="btn-secondary btn-sm"
                                title="Create NSE script scan items per service type (targets from host tags)"
                            >
                                <Target size={16} />
                                Generate Service Scans
                            </button>
                            <button
                                onClick={() => { setImportType('checklist'); setShowImportModal(true); }}
                                className="btn-secondary btn-sm"
                            >
                                <FileText size={16} />
                                Import
                            </button>
                        </div>

                        <div className="space-y-4">
                            {groups.length === 0 ? (
                                <div className="card text-center py-8">
                                    <FileText size={48} className="mx-auto text-dark-600 mb-4" />
                                    <p className="text-dark-400 mb-4">No checklist groups yet</p>
                                    <p className="text-dark-500 text-sm">Create a group or import from library to get started.</p>
                                </div>
                            ) : (
                                groups.map((group) => (
                                    <div key={group.id} ref={group.id === scrollToGroupId ? scrollToGroupRef : undefined} data-group-id={group.id}>
                                        <ChecklistGroupCard
                                        group={group}
                                        onSelectItem={(item, quickRun = false) => {
                                            if (quickRun) {
                                                // Quick Run: check if variables needed
                                                // For now, just try to execute with defaults
                                                handleExecuteItem(item, null, item.variables || {})
                                            } else {
                                                setSelectedItem(item)
                                                setTempCommand(item.command_template)
                                                setExecutionVariables({
                                                    ...(item.variables || {}),
                                                    ...(item.target_filter?.tags?.length ? { target_filter_tags: [...item.target_filter.tags] } : {})
                                                })
                                                setExecutionModalVariablesOpen(false)
                                                setShowExecutionPanel(true)
                                            }
                                        }}
                                        onViewOutput={(item) => {
                                            if (!item.latest_execution_id) {
                                                toast.error("No execution record found")
                                                return
                                            }
                                            setOutputModalState({
                                                open: true,
                                                executionId: item.latest_execution_id,
                                                itemId: item.id,
                                                title: `Output: ${item.name}`
                                            })
                                        }}
                                        selectedItems={selectedItems}
                                        onToggleSelect={(id) => {
                                            if (selectedItems.includes(id)) {
                                                setSelectedItems(selectedItems.filter(i => i !== id))
                                            } else {
                                                setSelectedItems([...selectedItems, id])
                                            }
                                        }}
                                        getStatusIcon={getStatusIcon}
                                        onStopExecution={handleStopExecution}
                                        onDeleteGroup={(id, name) => handleDeleteClick('group', id, name)}
                                        onDeleteItem={(id, name) => handleDeleteClick('item', id, name)}
                                        currentUserId={user?.id}
                                        batchProgress={batchProgress}
                                        onForceTakeover={async (item) => {
                                            const approved = confirm(`Force takeover for "${item.name}" and run now?`)
                                            if (approved) {
                                                try {
                                                    await checklistsApi.takeoverItem(item.id, { host_id: null })
                                                    handleExecuteItem(item, null, item.variables || {}, null, false)
                                                } catch (error) {
                                                    toast.error(error.response?.data?.detail || 'Failed to take over item')
                                                }
                                            }
                                        }}
                                    />
                                    </div>
                                ))
                            )}
                        </div>
                    </div>

                    {/* Sidebar */}
                    <div className="space-y-4">
                        {/* Bulk Actions */}
                        {selectedItems.length > 0 && (
                            <div className="card bg-accent-primary text-white border-0 animate-slide-in">
                                <div className="flex items-center justify-between">
                                    <span className="font-semibold">{selectedItems.length} Selected</span>
                                    <div className="flex gap-2">
                                        <button onClick={() => setSelectedItems([])} className="btn-ghost btn-sm text-white hover:bg-white/10">
                                            Cancel
                                        </button>
                                        <button onClick={handleBulkExecute} className="btn-success btn-sm bg-white text-accent-primary hover:bg-white/90">
                                            <Play size={14} className="mr-1" />
                                            Run All
                                        </button>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Progress Card */}
                        <div className="card">
                            <h3 className="font-semibold text-dark-100 mb-4">Progress</h3>
                            <div className="space-y-3">
                                <div>
                                    <div className="flex justify-between text-sm mb-1">
                                        <span className="text-dark-400">Overall</span>
                                        <span className="text-dark-300">{Math.round(project?.checklist_progress || 0)}%</span>
                                    </div>
                                    <div className="progress-bar">
                                        <div className="progress-fill" style={{ width: `${project?.checklist_progress || 0}%` }}></div>
                                    </div>
                                </div>
                                {groups.map((group) => (
                                    <div key={group.id}>
                                        <div className="flex justify-between text-sm mb-1">
                                            <span className="text-dark-500 text-xs">{group.name}</span>
                                            <span className="text-dark-400 text-xs">{group.completion_stats?.percentage || 0}%</span>
                                        </div>
                                        <div className="progress-bar h-1">
                                            <div className="progress-fill" style={{ width: `${group.completion_stats?.percentage || 0}%` }}></div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>

                        {user && (
                            <div className="card">
                                <h3 className="font-semibold text-dark-100 mb-3">Collaborators</h3>
                                <div className="space-y-2 max-h-40 overflow-y-auto mb-3">
                                    {members.map((member) => (
                                        <div key={member.id} className="flex items-center justify-between text-sm bg-dark-900 border border-dark-700 rounded px-2 py-1.5">
                                            <span className="text-dark-200">{member.username}</span>
                                            {member.user_id !== user.id && (
                                                <button
                                                    onClick={() => handleRemoveMember(member)}
                                                    className="text-xs text-accent-danger hover:text-accent-danger/80"
                                                >
                                                    remove
                                                </button>
                                            )}
                                        </div>
                                    ))}
                                    {members.length === 0 && (
                                        <p className="text-xs text-dark-500">No collaborators yet.</p>
                                    )}
                                </div>
                                <div className="flex gap-2">
                                    <input
                                        className="input text-sm"
                                        placeholder="username or email"
                                        value={memberInput}
                                        onChange={(e) => setMemberInput(e.target.value)}
                                    />
                                    <button onClick={handleAddMember} className="btn-secondary btn-sm">
                                        Add
                                    </button>
                                </div>
                            </div>
                        )}


                    </div>
                </div>
            )
            }

            {/* Flows View */}
            {
                activeView === 'flows' && (
                    <div className="space-y-6">
                        <div className="flex items-center justify-between">
                            <h2 className="text-lg font-semibold text-dark-100">Project Flows</h2>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => { setImportType('flow'); setShowImportModal(true); }}
                                    className="btn-secondary btn-sm"
                                >
                                    <Workflow size={16} />
                                    Import
                                </button>
                            </div>
                        </div>

                        {projectFlows.length === 0 ? (
                            <div className="card text-center py-12">
                                <Workflow size={48} className="mx-auto text-dark-600 mb-4" />
                                <p className="text-dark-400">No flows in this project</p>
                                <p className="text-sm text-dark-500 mt-2">Create a new flow or import a template.</p>
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                                {projectFlows.map(flow => (
                                    <Link
                                        key={flow.id}
                                        to={`/flow/${flow.id}`}
                                        className="card group hover:border-accent-primary transition-colors relative"
                                    >
                                        <div className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                                            <button
                                                onClick={(e) => {
                                                    e.preventDefault()
                                                    e.stopPropagation()
                                                    handleDeleteFlow(flow.id)
                                                }}
                                                className="p-1.5 text-dark-400 hover:text-accent-danger hover:bg-dark-800 rounded transition-colors"
                                                title="Delete Flow"
                                            >
                                                <Trash2 size={16} />
                                            </button>
                                        </div>
                                        <div className="flex items-start gap-4 mb-4">
                                            <div className="p-3 rounded-lg bg-accent-primary/10 text-accent-primary">
                                                <Workflow size={24} />
                                            </div>
                                            <div className="flex-1 min-w-0 pr-8">
                                                <div className="flex items-center gap-2">
                                                    <h3 className="font-semibold text-dark-100 group-hover:text-accent-primary transition-colors truncate">
                                                        {flow.name}
                                                    </h3>
                                                    {flow.ad_domain_name && (
                                                        <span className="px-1.5 py-0.5 text-[10px] rounded bg-purple-500/15 text-purple-400 shrink-0">
                                                            {flow.ad_domain_name}
                                                        </span>
                                                    )}
                                                </div>
                                                <p className="text-sm text-dark-400 line-clamp-2">
                                                    {flow.description || 'No description'}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="pt-4 border-t border-dark-700 flex justify-between text-sm text-500">
                                            <span>{flow.steps?.length || 0} steps</span>
                                            <span>Updated {new Date(flow.updated_at).toLocaleDateString()}</span>
                                        </div>
                                    </Link>
                                ))}
                            </div>
                        )}
                    </div>
                )
            }

            {/* Assets View */}
            {
                activeView === 'assets' && (
                    <div className="space-y-4">
                        <AssetsView
                            hosts={hosts}
                            hostsTotal={hostsTotal}
                            hostsPage={hostsPage}
                            hostsPerPage={hostsPerPage}
                            hostsSortBy={hostsSortBy}
                            hostsSortOrder={hostsSortOrder}
                            hostsTagsFilter={hostsTagsFilter}
                            projectHostTags={projectHostTags}
                            projectId={projectId}
                            search={assetsSearch}
                            filterOptions={hostFilterOptions}
                            hostsSmbSigningFilter={hostsSmbSigningFilter}
                            hostsOsFilter={hostsOsFilter}
                            hostsPortSearch={hostsPortSearch}
                            hostsDomainFilter={hostsDomainFilter}
                            hostsScopeFilter={hostsScopeFilter}
                            onSearchChange={(term) => {
                                setAssetsSearch(term)
                                if (assetsSearchDebounceRef.current) clearTimeout(assetsSearchDebounceRef.current)
                                assetsSearchDebounceRef.current = setTimeout(() => {
                                    fetchHosts(projectId, { per_page: 100, page: 1, ...(term.trim() ? { search: term.trim() } : {}) })
                                }, 300)
                            }}
                            onFetchPage={(page) => fetchHosts(projectId, { per_page: 100, page, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })}
                            onSortChange={(column, direction) => {
                                setHostsSort(column, direction)
                                fetchHosts(projectId, { per_page: 100, page: 1, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })
                            }}
                            onTagsFilterChange={(tags) => {
                                setHostsTagsFilter(tags)
                                fetchHosts(projectId, { per_page: 100, page: 1, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })
                            }}
                            onSmbSigningFilterChange={(val) => {
                                setHostsSmbSigningFilter(val)
                                fetchHosts(projectId, { per_page: 100, page: 1, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })
                            }}
                            onOsFilterChange={(val) => {
                                setHostsOsFilter(val)
                                fetchHosts(projectId, { per_page: 100, page: 1, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })
                            }}
                            onPortSearchChange={(val) => {
                                setHostsPortSearch(val)
                                if (portSearchDebounceRef.current) clearTimeout(portSearchDebounceRef.current)
                                portSearchDebounceRef.current = setTimeout(() => {
                                    fetchHosts(projectId, { per_page: 100, page: 1, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })
                                }, 300)
                            }}
                            onDomainFilterChange={(val) => {
                                setHostsDomainFilter(val)
                                fetchHosts(projectId, { per_page: 100, page: 1, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })
                            }}
                            onScopeFilterChange={(val) => {
                                setHostsScopeFilter(val)
                                fetchHosts(projectId, { per_page: 100, page: 1, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })
                            }}
                            onClearFilters={() => {
                                setHostsSmbSigningFilter('')
                                setHostsOsFilter('')
                                setHostsPortSearch('')
                                setHostsDomainFilter('')
                                setHostsScopeFilter('')
                                fetchHosts(projectId, { per_page: 100, page: 1, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })
                            }}
                            onSetScope={async (hostIds, excluded) => {
                                try {
                                    const { data } = await hostsApi.bulkScope(hostIds, excluded)
                                    toast.success(`${data.updated} host${data.updated !== 1 ? 's' : ''} ${excluded ? 'excluded from' : 'returned to'} scope`)
                                    fetchHosts(projectId, { per_page: 100, page: hostsPage, ...(assetsSearch.trim() ? { search: assetsSearch.trim() } : {}) })
                                    refreshProjectSummary()
                                } catch (error) {
                                    toast.error(error.response?.data?.detail || 'Failed to update scope')
                                }
                            }}
                            onExportIps={async (hostIds) => {
                                try {
                                    const { data } = await projectsApi.exportIps(projectId, hostIds)
                                    toast.success(`Exported ${data.count} IP${data.count !== 1 ? 's' : ''} to ${data.path}`)
                                } catch (error) {
                                    toast.error(error.response?.data?.detail || 'Failed to export IPs')
                                }
                            }}
                            onAddHost={handleCreateHost}
                            onDeleteHost={handleDeleteHost}
                            onExportServices={async () => {
                                try {
                                    const { data } = await projectsApi.exportServices(projectId)
                                    toast.success(data.message || 'Services exported successfully')
                                } catch (error) {
                                    console.error(error)
                                    toast.error('Failed to export services')
                                }
                            }}
                        />
                    </div>
                )
            }

            {/* Variables View */}
            {
                activeView === 'variables' && (
                    <ProjectVariables projectId={projectId} />
                )
            }

            {/* Execution Panel Modal */}
            {
                showExecutionPanel && selectedItem && (
                    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4 overflow-y-auto">
                        <div className="card max-w-lg w-full max-h-[90vh] flex flex-col animate-slide-in my-auto">
                            <div className="flex items-center justify-between mb-4 flex-shrink-0">
                                <h2 className="text-xl font-bold text-dark-50">Execute Task</h2>
                                <button onClick={handleCloseExecutionPanel} className="btn-ghost btn-sm">
                                    ✕
                                </button>
                            </div>

                            <div className="space-y-4 flex-1 min-h-0 overflow-y-auto pr-1">
                                <div className="flex-shrink-0">
                                    <h3 className="font-medium text-dark-200">{selectedItem.name}</h3>
                                    <p className="text-sm text-dark-400">{selectedItem.description}</p>
                                </div>

                                {selectedItem.claim?.is_active &&
                                    selectedItem.claim.claimed_by_user_id !== user?.id && (
                                        <div className="p-3 rounded-lg border border-accent-warning/30 bg-accent-warning/10 text-xs text-accent-warning">
                                            Claimed by {selectedItem.claim.claimed_by_username || 'another operator'}.
                                            Running this task will require force takeover confirmation.
                                        </div>
                                    )}

                                <div className="bg-dark-950 rounded-lg p-4 font-mono text-sm relative group flex-shrink-0">
                                    <div className="flex justify-between items-center mb-1">
                                        <div className="text-dark-500">Command:</div>
                                        {!isEditingCommand && (
                                            <button
                                                onClick={() => {
                                                    setTempCommand(selectedItem.command_template)
                                                    setIsEditingCommand(true)
                                                }}
                                                className="text-xs text-accent-primary hover:text-accent-primary/80"
                                            >
                                                Edit
                                            </button>
                                        )}
                                    </div>

                                    {isEditingCommand ? (
                                        <div className="space-y-2">
                                            <textarea
                                                className="w-full bg-dark-900 border border-dark-700 rounded p-2 text-accent-info focus:outline-none focus:border-accent-primary"
                                                value={tempCommand}
                                                onChange={(e) => setTempCommand(e.target.value)}
                                                rows={3}
                                            />
                                            <div className="flex gap-2 justify-end">
                                                <button
                                                    onClick={() => {
                                                        setIsEditingCommand(false)
                                                        setTempCommand(selectedItem.command_template)
                                                    }}
                                                    className="text-xs text-dark-400 hover:text-white"
                                                >
                                                    Cancel
                                                </button>
                                                <button
                                                    onClick={() => setIsEditingCommand(false)}
                                                    className="text-xs text-accent-success hover:text-accent-success/80"
                                                >
                                                    Done
                                                </button>
                                            </div>
                                        </div>
                                    ) : (
                                        <div className="text-accent-info break-all">
                                            {tempCommand && tempCommand !== selectedItem.command_template
                                                ? <span className="text-accent-warning" title="Modified">{tempCommand} *</span>
                                                : selectedItem.command_template
                                            }
                                        </div>
                                    )}
                                </div>

                                {Object.keys(selectedItem.variables || {}).length > 0 && (
                                    <div className="flex-shrink-0">
                                        <button
                                            type="button"
                                            onClick={() => setExecutionModalVariablesOpen(!executionModalVariablesOpen)}
                                            className="flex items-center gap-2 w-full text-left py-1 rounded hover:bg-dark-800/50 transition-colors"
                                        >
                                            {executionModalVariablesOpen ? <ChevronDown size={18} className="text-dark-400" /> : <ChevronRight size={18} className="text-dark-400" />}
                                            <span className="text-sm font-medium text-dark-300">Variables</span>
                                            <span className="text-dark-500 text-xs">({Object.keys(selectedItem.variables || {}).length})</span>
                                        </button>
                                        {executionModalVariablesOpen && (
                                            <div className="mt-2 space-y-2 pl-5">
                                                {Object.entries(selectedItem.variables).map(([key, defaultVal]) => (
                                                    <div key={key}>
                                                        <label className="text-xs text-dark-500">{key}</label>
                                                        <input
                                                            type="text"
                                                            className="input"
                                                            placeholder={String(defaultVal)}
                                                            value={executionVariables[key] || ''}
                                                            onChange={(e) => setExecutionVariables({
                                                                ...executionVariables,
                                                                [key]: e.target.value
                                                            })}
                                                        />
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}

                                {(listForTarget.length > 0 || executionPanelHostsLoading) && (() => {
                                    const selectedTags = executionVariables.target_filter_tags || []
                                    const matchingHostCount = selectedTags.length === 0
                                        ? 0
                                        : listForTarget.filter(h => (h.tags || []).some(t => selectedTags.includes(t))).length
                                    return (
                                    <div>
                                        <label className="block text-sm font-medium text-dark-300 mb-2">Target by tags (hosts matching any selected tag)</label>
                                        <div className="max-h-32 overflow-y-auto border border-dark-700 rounded-lg p-2 bg-dark-900 flex flex-wrap gap-2">
                                            {[...new Set(listForTarget.flatMap(h => h.tags || []))].sort().map(tag => (
                                                <label key={tag} className="flex items-center gap-1.5 cursor-pointer">
                                                    <input
                                                        type="checkbox"
                                                        checked={selectedTags.includes(tag)}
                                                        onChange={(e) => {
                                                            const current = executionVariables.target_filter_tags || []
                                                            const next = e.target.checked ? [...current, tag] : current.filter(t => t !== tag)
                                                            setExecutionVariables({ ...executionVariables, target_filter_tags: next })
                                                        }}
                                                    />
                                                    <span className="text-sm text-dark-300">{tag}</span>
                                                </label>
                                            ))}
                                            {[...new Set(listForTarget.flatMap(h => h.tags || []))].length === 0 && !executionPanelHostsLoading && (
                                                <span className="text-dark-500 text-sm">No tags on hosts yet (run discovery first)</span>
                                            )}
                                        </div>
                                        <p className="text-xs text-dark-500 mt-1">
                                            {selectedTags.length > 0
                                                ? `${matchingHostCount} host${matchingHostCount !== 1 ? 's' : ''} match selected tag${selectedTags.length !== 1 ? 's' : ''}. Targets resolved at run time.`
                                                : 'Select tags to target hosts. Targets will be resolved at run time.'}
                                        </p>
                                    </div>
                                    )
                                })()}

                                {(listForTarget.length > 0 || executionPanelHostsLoading) && (() => {
                                    const selectedTags = executionVariables.target_filter_tags || []
                                    const execDomains = [...new Set(listForTarget.map(h => h.extra_data?.domain).filter(Boolean))].sort()
                                    const hostsForTargetList = (selectedTags.length > 0
                                        ? listForTarget.filter(h => (h.tags || []).some(t => selectedTags.includes(t)))
                                        : listForTarget
                                    ).filter(h => !executeDomainFilter || h.extra_data?.domain === executeDomainFilter)
                                    return (
                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between">
                                            <label className="block text-sm font-medium text-dark-300">Target Hosts <span className="text-dark-500">({hostsForTargetList.length})</span></label>
                                            {execDomains.length > 0 && (
                                                <select
                                                    value={executeDomainFilter}
                                                    onChange={(e) => setExecuteDomainFilter(e.target.value)}
                                                    className="input py-1 px-2 text-xs bg-dark-900 border-dark-700 rounded min-w-[130px]"
                                                >
                                                    <option value="">Domain: All</option>
                                                    {execDomains.map(d => (
                                                        <option key={d} value={d}>{d}</option>
                                                    ))}
                                                </select>
                                            )}
                                        </div>

                                        {executionPanelHostsLoading && (
                                            <div className="flex items-center gap-2 py-2 text-dark-400 text-sm">
                                                <Loader2 size={16} className="animate-spin" />
                                                Loading hosts…
                                            </div>
                                        )}

                                        {/* Search & Bulk Actions */}
                                        <div className="flex items-center gap-2 mb-2">
                                            <div className="relative flex-1">
                                                <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-dark-500" />
                                                <input
                                                    type="text"
                                                    placeholder="Search targets..."
                                                    className="input-sm w-full pl-8 bg-dark-900 border-dark-700 rounded text-xs"
                                                    value={targetSearch}
                                                    onChange={(e) => setTargetSearch(e.target.value)}
                                                />
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    const current = executionVariables._selected_hosts || []
                                                    const filteredIds = hostsForTargetList
                                                        .filter(h =>
                                                            (h.display_name || '').toLowerCase().includes(targetSearch.toLowerCase()) ||
                                                            (h.ip_address || '').toLowerCase().includes(targetSearch.toLowerCase())
                                                        )
                                                        .map(h => h.id)

                                                    // Add filtered IDs that aren't already selected
                                                    const newSelection = [...new Set([...current, ...filteredIds])]

                                                    // Auto-populate target var
                                                    const targetVar = Object.keys(selectedItem.variables || {}).find(k => k.includes('target') || k.includes('scope')) || 'target'
                                                    const selectedIPs = listForTarget.filter(h => newSelection.includes(h.id)).map(h => h.ip_address || h.display_name)

                                                    setExecutionVariables({
                                                        ...executionVariables,
                                                        _selected_hosts: newSelection,
                                                        [targetVar]: selectedIPs
                                                    })
                                                }}
                                                className="btn-xs btn-secondary text-xs px-2 py-1"
                                            >
                                                Select All
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    const current = executionVariables._selected_hosts || []
                                                    const filteredIds = hostsForTargetList
                                                        .filter(h =>
                                                            (h.display_name || '').toLowerCase().includes(targetSearch.toLowerCase()) ||
                                                            (h.ip_address || '').toLowerCase().includes(targetSearch.toLowerCase())
                                                        )
                                                        .map(h => h.id)

                                                    const newSelection = current.filter(id => !filteredIds.includes(id))

                                                    // Auto-populate target var
                                                    const targetVar = Object.keys(selectedItem.variables || {}).find(k => k.includes('target') || k.includes('scope')) || 'target'
                                                    const selectedIPs = listForTarget.filter(h => newSelection.includes(h.id)).map(h => h.ip_address || h.display_name)

                                                    setExecutionVariables({
                                                        ...executionVariables,
                                                        _selected_hosts: newSelection,
                                                        [targetVar]: selectedIPs
                                                    })
                                                }}
                                                className="btn-xs btn-ghost text-dark-400 text-xs px-2 py-1"
                                            >
                                                Clear
                                            </button>
                                        </div>

                                        <div className="max-h-48 overflow-y-auto border border-dark-700 rounded-lg p-2 bg-dark-900">
                                            {hostsForTargetList
                                                .filter(host =>
                                                    (host.display_name || '').toLowerCase().includes(targetSearch.toLowerCase()) ||
                                                    (host.ip_address || '').toLowerCase().includes(targetSearch.toLowerCase())
                                                )
                                                .map((host) => (
                                                    <div key={host.id} className="flex items-center gap-2 py-1">
                                                        <input
                                                            type="checkbox"
                                                            id={`exec-host-${host.id}`}
                                                            checked={(executionVariables._selected_hosts || []).includes(host.id)}
                                                            onChange={(e) => {
                                                                const current = executionVariables._selected_hosts || []
                                                                const newSelection = e.target.checked
                                                                    ? [...current, host.id]
                                                                    : current.filter(id => id !== host.id)

                                                                // Auto-populate target variable if defined
                                                                const targetVar = Object.keys(selectedItem.variables || {}).find(k => k.includes('target') || k.includes('scope')) || 'target'
                                                                const selectedIPs = listForTarget.filter(h =>
                                                                    (e.target.checked ? [...current, host.id] : current.filter(id => id !== host.id)).includes(h.id)
                                                                ).map(h => h.ip_address || h.display_name)

                                                                setExecutionVariables({
                                                                    ...executionVariables,
                                                                    _selected_hosts: newSelection,
                                                                    [targetVar]: selectedIPs
                                                                })
                                                            }}
                                                        />
                                                        <label htmlFor={`exec-host-${host.id}`} className="text-sm text-dark-300 cursor-pointer select-none flex items-center gap-1.5">
                                                            {host.display_name} <span className="text-dark-500 text-xs">({host.ip_address})</span>
                                                            {host.extra_data?.domain && <span className="px-1 py-0.5 text-[10px] rounded bg-purple-500/15 text-purple-400">{host.extra_data.domain}</span>}
                                                        </label>
                                                    </div>
                                                ))}
                                            {hostsForTargetList.filter(host =>
                                                (host.display_name || '').toLowerCase().includes(targetSearch.toLowerCase()) ||
                                                (host.ip_address || '').toLowerCase().includes(targetSearch.toLowerCase())
                                            ).length === 0 && !executionPanelHostsLoading && (
                                                    <div className="text-center py-4 text-dark-500 text-sm">
                                                        No matching targets found
                                                    </div>
                                                )}
                                        </div>
                                        {hostsForTargetList.length !== listForTarget.length && (selectedTags.length > 0 || executeDomainFilter) && (
                                            <p className="text-xs text-dark-500">
                                                Showing only hosts matching selected filter(s).
                                            </p>
                                        )}
                                        <p className="text-xs text-dark-500">
                                            Selected IPs will be injected into the 'target' variable by default.
                                        </p>
                                    </div>
                                    )
                                })()}
                            </div>

                            <div className="flex gap-3 pt-4 flex-shrink-0 border-t border-dark-700 mt-4 pt-4">
                                <button onClick={handleCloseExecutionPanel} className="btn-secondary flex-1">
                                    Cancel
                                </button>
                                <button onClick={() => {
                                    // Clean up internal _selected_hosts before sending
                                    const { _selected_hosts, ...varsToSend } = executionVariables
                                    // Determine host_id: if exactly 1 selected, use it (for linking execution). If >1, use null (global).
                                    const hostId = (_selected_hosts?.length === 1) ? _selected_hosts[0] : null

                                    const commandToSend = (tempCommand && tempCommand !== selectedItem.command_template) ? tempCommand : null

                                    handleExecuteItem(selectedItem, hostId, varsToSend, commandToSend)
                                }} className="btn-success flex-1">
                                    <Play size={18} />
                                    Execute
                                </button>
                            </div>
                        </div>
                    </div>
                )
            }

            {/* Import Modal */}
            {
                showImportModal && (
                    <ImportModal
                        type={importType}
                        projectId={projectId}
                        onClose={() => setShowImportModal(false)}
                        onSuccess={() => {
                            if (importType === 'checklist') fetchGroups(projectId)
                            if (importType === 'flow') fetchFlows(projectId)
                        }}
                    />
                )
            }

            {/* Output Modal */}
            {
                outputModalState.open && (
                    <OutputModal
                        executionId={outputModalState.executionId}
                        itemId={outputModalState.itemId}
                        title={outputModalState.title}
                        onClose={() => setOutputModalState({ ...outputModalState, open: false })}
                    />
                )
            }

            {/* Delete Confirmation Modal */}
            {
                deleteModalState.open && (
                    <DeleteConfirmationModal
                        name={deleteModalState.name}
                        type={deleteModalState.type}
                        onClose={() => setDeleteModalState({ ...deleteModalState, open: false })}
                        onConfirm={handleConfirmDelete}
                    />
                )
            }
        </div >
    )
}

// Parse IP for numeric comparison (returns null if not a valid IPv4)
function ipToSortKey(ip) {
    if (!ip || typeof ip !== 'string') return null
    const parts = ip.trim().split('.')
    if (parts.length !== 4) return null
    const nums = parts.map(p => parseInt(p, 10))
    if (nums.some(n => Number.isNaN(n) || n < 0 || n > 255)) return null
    return nums[0] * 16777216 + nums[1] * 65536 + nums[2] * 256 + nums[3]
}

function AssetsView({ hosts, hostsTotal = 0, hostsPage = 1, hostsPerPage = 100, hostsSortBy = null, hostsSortOrder = 'desc', hostsTagsFilter = [], projectHostTags = [], projectId, search: searchProp = '', onSearchChange, onFetchPage, onSortChange, onTagsFilterChange, onAddHost, onDeleteHost, onExportServices, filterOptions = {}, hostsSmbSigningFilter = null, hostsOsFilter = null, hostsPortSearch = null, hostsDomainFilter = null, hostsScopeFilter = null, onSmbSigningFilterChange, onOsFilterChange, onPortSearchChange, onDomainFilterChange, onScopeFilterChange, onClearFilters, onSetScope, onExportIps }) {
    const [viewMode, setViewMode] = useState(localStorage.getItem('assetViewMode') || 'grid')
    const [selectedIds, setSelectedIds] = useState([])
    const [localSearch, setLocalSearch] = useState('')
    const search = onSearchChange != null ? searchProp : localSearch
    const setSearch = onSearchChange != null ? onSearchChange : setLocalSearch
    const [localSortColumn, setLocalSortColumn] = useState(null)
    const [localSortDirection, setLocalSortDirection] = useState('asc')

    const serverTotalPages = Math.ceil((hostsTotal ?? 0) / (hostsPerPage || 100)) || 1
    const hasServerPagination = onFetchPage && serverTotalPages > 1
    const sortColumn = hasServerPagination ? hostsSortBy : localSortColumn
    const sortDirection = hasServerPagination ? hostsSortOrder : localSortDirection
    // Always use store's hostsTagsFilter when onTagsFilterChange is provided (server-side filter)
    const selectedTags = onTagsFilterChange ? hostsTagsFilter : []

    const availableTags = useMemo(
        () => (projectHostTags?.length > 0 ? [...projectHostTags].sort() : [...new Set((hosts || []).flatMap(h => h.tags || []))].sort()),
        [projectHostTags, hosts]
    )

    const handleTagsFilterChange = (newTags) => {
        if (onTagsFilterChange) {
            onTagsFilterChange(newTags)
        }
    }

    useEffect(() => {
        localStorage.setItem('assetViewMode', viewMode)
    }, [viewMode])

    // When onSearchChange is provided, search is server-side (whole discovered hosts); otherwise filter current page only
    const filteredHosts = useMemo(() => {
        if (!hosts) return []
        if (onSearchChange != null) return hosts
        const term = (search || '').toLowerCase()
        if (!term) return hosts
        return hosts.filter(host => (
            (host.display_name || '').toLowerCase().includes(term) ||
            (host.ip_address || '').toLowerCase().includes(term) ||
            (host.os_info || '').toLowerCase().includes(term)
        ))
    }, [hosts, search, onSearchChange])

    const sortedHosts = useMemo(() => {
        if (hasServerPagination) return filteredHosts
        if (!sortColumn) return filteredHosts
        const dir = sortDirection === 'asc' ? 1 : -1
        return [...filteredHosts].sort((a, b) => {
            let cmp = 0
            switch (sortColumn) {
                case 'host':
                    cmp = (a.display_name || '').localeCompare(b.display_name || '')
                    break
                case 'ip_address': {
                    const ka = ipToSortKey(a.ip_address)
                    const kb = ipToSortKey(b.ip_address)
                    if (ka != null && kb != null) cmp = ka - kb
                    else if (ka != null) cmp = -1
                    else if (kb != null) cmp = 1
                    else cmp = (a.ip_address || '').localeCompare(b.ip_address || '')
                    break
                }
                case 'os_info':
                    cmp = (a.os_info || '').localeCompare(b.os_info || '')
                    break
                case 'smb_signing': {
                    const va = a.extra_data?.smb_signing
                    const vb = b.extra_data?.smb_signing
                    const order = (v) => (v === true ? 2 : v === false ? 1 : 0)
                    cmp = order(va) - order(vb)
                    break
                }
                case 'service_count':
                    cmp = (a.service_count ?? 0) - (b.service_count ?? 0)
                    break
                case 'execution_count':
                    cmp = (a.execution_count ?? 0) - (b.execution_count ?? 0)
                    break
                case 'file_count':
                    cmp = (a.file_count ?? 0) - (b.file_count ?? 0)
                    break
                default:
                    return 0
            }
            return cmp * dir
        })
    }, [filteredHosts, sortColumn, sortDirection, hasServerPagination])

    const paginatedHosts = sortedHosts

    // Selection is per-page: hosts arrive as a server-paginated, server-filtered slice,
    // so an id kept across a page change would act on a row nobody can see.
    useEffect(() => {
        setSelectedIds(prev => prev.filter(id => paginatedHosts.some(h => h.id === id)))
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [hostsPage, hostsTotal, hosts])

    const selectedHosts = paginatedHosts.filter(h => selectedIds.includes(h.id))
    const allOnPageSelected = paginatedHosts.length > 0 && selectedIds.length === paginatedHosts.length
    const toggleOne = (id) => setSelectedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
    const toggleAllOnPage = () => setSelectedIds(allOnPageSelected ? [] : paginatedHosts.map(h => h.id))

    const handleSetScope = async (excluded) => {
        if (!onSetScope || selectedIds.length === 0) return
        await onSetScope(selectedIds, excluded)
        setSelectedIds([])
    }

    // Excluded hosts stay selectable — that is how Include works — so the export side
    // is what defends. Both exports carry the same in-scope-only list: a targets file
    // holding an out-of-scope IP is the same mistake as scanning one.
    const exportableHosts = selectedHosts.filter(h => !h.excluded)
    const exportableIps = [...new Set(exportableHosts.map(h => h.ip_address).filter(Boolean))].sort()
    const excludedInSelection = selectedIds.length - exportableHosts.length

    // Browser download: no round trip, and the analyst gets the list where they are.
    // The project-folder copy (onExportIps) is the one tools read with -iL.
    const handleDownloadIps = () => {
        if (exportableIps.length === 0) return
        const url = URL.createObjectURL(new Blob([exportableIps.join('\n') + '\n'], { type: 'text/plain' }))
        const a = document.createElement('a')
        a.href = url
        a.download = `targets_project_${projectId}.txt`
        a.click()
        URL.revokeObjectURL(url)
        toast.success(`Downloaded ${exportableIps.length} IP${exportableIps.length !== 1 ? 's' : ''}`)
    }

    const SelectCheckbox = ({ host }) => (
        <input
            type="checkbox"
            checked={selectedIds.includes(host.id)}
            onChange={() => toggleOne(host.id)}
            onClick={(e) => e.stopPropagation()}
            className="rounded border-dark-600 bg-dark-800 text-accent-primary cursor-pointer"
            title="Select host"
        />
    )

    const ExcludedBadge = () => (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded bg-accent-danger/15 text-accent-danger whitespace-nowrap">
            <Ban size={10} />
            Excluded
        </span>
    )

    const handleSort = (column) => {
        if (hasServerPagination && onSortChange) {
            const newDirection = sortColumn === column && sortDirection === 'asc' ? 'desc' : 'asc'
            onSortChange(column, newDirection)
        } else {
            setLocalSortColumn(column)
            setLocalSortDirection(prev => sortColumn === column ? (prev === 'asc' ? 'desc' : 'asc') : 'asc')
        }
    }

    const SortableTh = ({ column, label }) => (
        <th
            className="px-4 py-3 cursor-pointer select-none hover:text-dark-200 transition-colors"
            onClick={() => handleSort(column)}
        >
            <span className="inline-flex items-center gap-1">
                {label}
                {sortColumn === column
                    ? (sortDirection === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)
                    : <ArrowUpDown size={12} className="opacity-50" />}
            </span>
        </th>
    )

    return (
        <div className="space-y-6">
            <div className="flex flex-col md:flex-row gap-4 items-center justify-between">
                <div className="flex items-center gap-4 w-full md:w-auto">
                    <h2 className="text-lg font-semibold text-dark-100 whitespace-nowrap">
                        Discovered Hosts ({hostsTotal != null && hostsTotal > 0 ? hostsTotal : filteredHosts.length})
                    </h2>
                    <div className="relative flex-1 md:w-64">
                        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-dark-500" />
                        <input
                            type="text"
                            placeholder="Search hosts..."
                            className="input pl-9 py-1.5 w-full"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                    </div>
                </div>

                {availableTags.length > 0 && (
                    <div className="flex flex-wrap items-center gap-2 w-full">
                        <span className="text-sm text-dark-400 whitespace-nowrap">Filter by tag:</span>
                        {availableTags.map(tag => (
                            <button
                                key={tag}
                                type="button"
                                onClick={() => handleTagsFilterChange(
                                    selectedTags.includes(tag) ? selectedTags.filter(t => t !== tag) : [...selectedTags, tag]
                                )}
                                className={`px-2.5 py-1 rounded-md text-sm font-medium transition-colors ${
                                    selectedTags.includes(tag)
                                        ? 'bg-accent-primary text-white'
                                        : 'bg-dark-700 text-dark-300 hover:bg-dark-600 hover:text-dark-100'
                                }`}
                            >
                                {tag}
                            </button>
                        ))}
                        {selectedTags.length > 0 && (
                            <button
                                type="button"
                                onClick={() => handleTagsFilterChange([])}
                                className="text-sm text-dark-400 hover:text-dark-200 underline"
                            >
                                Clear
                            </button>
                        )}
                    </div>
                )}

                {(filterOptions.smb_signing_values?.length > 0 || filterOptions.os_values?.length > 0 || filterOptions.domain_values?.length > 0 || onPortSearchChange || onScopeFilterChange) && (
                    <div className="flex flex-wrap items-center gap-3 w-full">
                        <Filter size={14} className="text-dark-500" />
                        {onScopeFilterChange && (
                            <select
                                value={hostsScopeFilter || ''}
                                onChange={(e) => onScopeFilterChange(e.target.value)}
                                className="input py-1.5 px-3 text-sm bg-dark-800 border-dark-700 rounded-lg min-w-[160px]"
                            >
                                <option value="">Scope: All</option>
                                <option value="false">In scope</option>
                                <option value="true">Excluded</option>
                            </select>
                        )}
                        {filterOptions.domain_values?.length > 0 && (
                            <select
                                value={hostsDomainFilter || ''}
                                onChange={(e) => onDomainFilterChange?.(e.target.value)}
                                className="input py-1.5 px-3 text-sm bg-dark-800 border-dark-700 rounded-lg min-w-[160px]"
                            >
                                <option value="">Domain: All</option>
                                {filterOptions.domain_values.map(val => (
                                    <option key={val} value={val}>{val}</option>
                                ))}
                            </select>
                        )}
                        {filterOptions.smb_signing_values?.length > 0 && (
                            <select
                                value={hostsSmbSigningFilter || ''}
                                onChange={(e) => onSmbSigningFilterChange?.(e.target.value)}
                                className="input py-1.5 px-3 text-sm bg-dark-800 border-dark-700 rounded-lg min-w-[160px]"
                            >
                                <option value="">SMB Signing: All</option>
                                {filterOptions.smb_signing_values.map(val => (
                                    <option key={val} value={val}>
                                        {val === 'true' ? 'Enabled' : val === 'false' ? 'Disabled' : 'Unknown'}
                                    </option>
                                ))}
                            </select>
                        )}
                        {filterOptions.os_values?.length > 0 && (
                            <select
                                value={hostsOsFilter || ''}
                                onChange={(e) => onOsFilterChange?.(e.target.value)}
                                className="input py-1.5 px-3 text-sm bg-dark-800 border-dark-700 rounded-lg min-w-[160px] max-w-[280px]"
                            >
                                <option value="">OS: All</option>
                                {filterOptions.os_values.map(val => (
                                    <option key={val} value={val}>{val}</option>
                                ))}
                            </select>
                        )}
                        {onPortSearchChange && (
                            <div className="relative">
                                <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dark-500" />
                                <input
                                    type="number"
                                    placeholder="Search by port..."
                                    className="input pl-8 py-1.5 text-sm w-40 bg-dark-800 border-dark-700 rounded-lg [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                                    value={hostsPortSearch ?? ''}
                                    onChange={(e) => onPortSearchChange(e.target.value)}
                                />
                            </div>
                        )}
                        {(hostsSmbSigningFilter || hostsOsFilter || hostsDomainFilter || hostsPortSearch != null || hostsScopeFilter) && (
                            <button
                                type="button"
                                onClick={() => onClearFilters?.()}
                                className="flex items-center gap-1 text-sm text-dark-400 hover:text-dark-200 transition-colors"
                            >
                                <X size={14} />
                                Clear filters
                            </button>
                        )}
                    </div>
                )}

                <div className="flex items-center gap-3 w-full md:w-auto justify-end">
                    <div className="flex bg-dark-800 rounded-lg p-1">
                        <button
                            onClick={() => setViewMode('grid')}
                            className={`p-1.5 rounded transition-colors ${viewMode === 'grid' ? 'bg-dark-700 text-white' : 'text-dark-400 hover:text-dark-200'}`}
                            title="Grid View"
                        >
                            <Grid3X3 size={18} />
                        </button>
                        <button
                            onClick={() => setViewMode('list')}
                            className={`p-1.5 rounded transition-colors ${viewMode === 'list' ? 'bg-dark-700 text-white' : 'text-dark-400 hover:text-dark-200'}`}
                            title="List View"
                        >
                            <List size={18} />
                        </button>
                    </div>
                    <button onClick={onAddHost} className="btn-primary btn-sm whitespace-nowrap">
                        <Plus size={16} />
                        Add Host
                    </button>
                    <button onClick={onExportServices} className="btn-secondary btn-sm whitespace-nowrap" title="Export service lists to project folder">
                        <Download size={16} />
                        Export Services
                    </button>
                </div>
            </div>

            {selectedIds.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 px-3 py-2 rounded-lg bg-dark-800/60 border border-dark-700">
                    <span className="text-sm text-dark-300 font-medium">
                        {selectedIds.length} selected
                    </span>
                    <button
                        type="button"
                        onClick={() => setSelectedIds([])}
                        className="text-xs text-dark-400 hover:text-dark-200 underline mr-2"
                    >
                        Clear
                    </button>
                    <div className="flex-1" />
                    {excludedInSelection > 0 && (
                        <span className="text-xs text-dark-400 mr-1">
                            {excludedInSelection} excluded host{excludedInSelection !== 1 ? 's' : ''} in selection will not be exported
                        </span>
                    )}
                    <button
                        type="button"
                        onClick={handleDownloadIps}
                        disabled={exportableIps.length === 0}
                        className="btn-secondary btn-sm whitespace-nowrap disabled:opacity-50"
                        title="Download the selected in-scope IPs as a .txt file"
                    >
                        <Download size={16} />
                        Download IPs ({exportableIps.length})
                    </button>
                    {onExportIps && (
                        <button
                            type="button"
                            onClick={() => onExportIps(exportableHosts.map(h => h.id))}
                            disabled={exportableIps.length === 0}
                            className="btn-secondary btn-sm whitespace-nowrap disabled:opacity-50"
                            title="Write the selected in-scope IPs to <project>/scope/targets.txt on the host"
                        >
                            <FolderDown size={16} />
                            Save IPs to project ({exportableIps.length})
                        </button>
                    )}
                    {onSetScope && selectedHosts.some(h => !h.excluded) && (
                        <button
                            type="button"
                            onClick={() => handleSetScope(true)}
                            className="btn-secondary btn-sm whitespace-nowrap text-accent-danger"
                            title="Mark out of scope: no checklist or flow execution may target these hosts"
                        >
                            <Ban size={16} />
                            Exclude
                        </button>
                    )}
                    {onSetScope && selectedHosts.some(h => h.excluded) && (
                        <button
                            type="button"
                            onClick={() => handleSetScope(false)}
                            className="btn-secondary btn-sm whitespace-nowrap text-accent-success"
                            title="Return these hosts to scope"
                        >
                            <ShieldCheck size={16} />
                            Include
                        </button>
                    )}
                </div>
            )}

            {filteredHosts.length === 0 ? (
                <div className="card text-center py-12">
                    <Server size={48} className="mx-auto text-dark-600 mb-4" />
                    <p className="text-dark-400 mb-2">
                        {selectedTags.length > 0
                            ? 'No hosts with selected tags'
                            : search
                                ? 'No matching hosts found'
                                : 'No hosts discovered yet'}
                    </p>
                    <p className="text-dark-500 text-sm mb-4">
                        {selectedTags.length > 0
                            ? 'Clear the tag filter or run service scans to discover more hosts'
                            : search
                                ? 'Try a different search term'
                                : 'Run reconnaissance tasks to discover hosts'}
                    </p>
                    {selectedTags.length > 0 && (
                        <button
                            type="button"
                            onClick={() => handleTagsFilterChange([])}
                            className="btn-secondary btn-sm"
                        >
                            Clear tag filter
                        </button>
                    )}
                </div>
            ) : (
                <>
                    {viewMode === 'grid' ? (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                            {paginatedHosts.map((host) => (
                                <Link
                                    key={host.id}
                                    to={`/host/${host.id}`}
                                    className={`card group hover:border-accent-primary/50 transition-all relative ${host.excluded ? 'opacity-60 border-accent-danger/30' : ''}`}
                                >
                                    <div className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                                        <button
                                            onClick={(e) => {
                                                e.preventDefault()
                                                e.stopPropagation()
                                                onDeleteHost(host.id)
                                            }}
                                            className="p-1.5 text-dark-400 hover:text-accent-danger hover:bg-dark-800 rounded transition-colors"
                                            title="Delete Host"
                                        >
                                            <Trash2 size={16} />
                                        </button>
                                    </div>
                                    <div className="flex items-start gap-3">
                                        {/* Card is a <Link>: the checkbox must not navigate. */}
                                        <div
                                            className="pt-2.5"
                                            onClick={(e) => { e.preventDefault(); e.stopPropagation() }}
                                        >
                                            <SelectCheckbox host={host} />
                                        </div>
                                        <div className="p-2 rounded-lg bg-accent-info/10">
                                            <Server size={20} className="text-accent-info" />
                                        </div>
                                        <div className="flex-1 min-w-0 pr-8">
                                            <h3 className="font-semibold text-dark-100 truncate group-hover:text-accent-primary">
                                                {host.display_name}
                                            </h3>
                                            {host.excluded && <div className="mt-1"><ExcludedBadge /></div>}
                                            {host.ip_address && (
                                                <p className="text-sm text-dark-400">{host.ip_address}</p>
                                            )}
                                            {host.os_info && (
                                                <p className="text-xs text-dark-500 mt-1">{host.os_info}</p>
                                            )}
                                            {host.extra_data?.domain && (
                                                <span className="inline-block mt-1 px-1.5 py-0.5 text-xs rounded bg-purple-500/15 text-purple-400">{host.extra_data.domain}</span>
                                            )}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-4 mt-4 pt-3 border-t border-dark-700 text-sm text-dark-500">
                                        <span>{host.service_count} services</span>
                                        <span>{host.execution_count} runs</span>
                                        <span>{host.file_count} files</span>
                                    </div>
                                </Link>
                            ))}
                        </div>
                    ) : (
                        <div className="card overflow-hidden p-0">
                            <table className="w-full text-left">
                                <thead className="bg-dark-800/50 text-xs uppercase text-dark-400 font-medium">
                                    <tr>
                                        <th className="px-4 py-3 w-10">
                                            <input
                                                type="checkbox"
                                                checked={allOnPageSelected}
                                                onChange={toggleAllOnPage}
                                                className="rounded border-dark-600 bg-dark-800 text-accent-primary cursor-pointer"
                                                title="Select all on this page"
                                            />
                                        </th>
                                        <SortableTh column="host" label="Host" />
                                        <SortableTh column="ip_address" label="IP Address" />
                                        <th className="px-4 py-3">Domain</th>
                                        <SortableTh column="os_info" label="OS" />
                                        <SortableTh column="smb_signing" label="SMB Signing" />
                                        <SortableTh column="service_count" label="Services" />
                                        <SortableTh column="execution_count" label="Activity" />
                                        <SortableTh column="file_count" label="Files" />
                                        <th className="px-4 py-3 text-right">Actions</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-dark-800">
                                    {paginatedHosts.map((host) => (
                                        <tr key={host.id} className={`group hover:bg-dark-800/30 transition-colors ${host.excluded ? 'opacity-60' : ''}`}>
                                            <td className="px-4 py-3">
                                                <SelectCheckbox host={host} />
                                            </td>
                                            <td className="px-4 py-3">
                                                <Link to={`/host/${host.id}`} className="flex items-center gap-3 font-medium text-dark-200 hover:text-accent-primary">
                                                    <Server size={16} className="text-dark-500" />
                                                    {host.display_name}
                                                    {host.excluded && <ExcludedBadge />}
                                                </Link>
                                            </td>
                                            <td className="px-4 py-3 text-sm text-dark-400 font-mono">
                                                {host.ip_address || '-'}
                                            </td>
                                            <td className="px-4 py-3 text-sm">
                                                {host.extra_data?.domain ? (
                                                    <span className="px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400 text-xs">{host.extra_data.domain}</span>
                                                ) : (
                                                    <span className="text-dark-500">—</span>
                                                )}
                                            </td>
                                            <td className="px-4 py-3 text-sm text-dark-400">
                                                {host.os_info || 'Unknown'}
                                            </td>
                                            <td className="px-4 py-3 text-sm">
                                                {host.extra_data?.smb_signing === true ? (
                                                    <span className="flex items-center gap-1.5 text-accent-success bg-accent-success/10 px-2 py-0.5 rounded-full w-fit text-xs font-medium">
                                                        <CheckCircle2 size={12} />
                                                        Enabled
                                                    </span>
                                                ) : host.extra_data?.smb_signing === false ? (
                                                    <span className="flex items-center gap-1.5 text-accent-danger bg-accent-danger/10 px-2 py-0.5 rounded-full w-fit text-xs font-medium">
                                                        <AlertCircle size={12} />
                                                        Disabled
                                                    </span>
                                                ) : (
                                                    <span className="text-dark-500">—</span>
                                                )}
                                            </td>
                                            <td className="px-4 py-3 text-sm text-dark-400">
                                                {host.service_count}
                                            </td>
                                            <td className="px-4 py-3 text-sm text-dark-400">
                                                {host.execution_count} runs
                                            </td>
                                            <td className="px-4 py-3 text-sm text-dark-400">
                                                {host.file_count} files
                                            </td>
                                            <td className="px-4 py-3 text-right">
                                                <button
                                                    onClick={() => onDeleteHost(host.id)}
                                                    className="p-1.5 text-dark-500 hover:text-accent-danger hover:bg-dark-800 rounded transition-colors opacity-0 group-hover:opacity-100"
                                                    title="Delete Host"
                                                >
                                                    <Trash2 size={16} />
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}

                    {/* Server-side pagination (when more hosts exist on server) */}
                    {hasServerPagination && (
                        <div className="flex justify-between items-center pt-4 border-t border-dark-800">
                            <span className="text-sm text-dark-500">
                                Showing {hosts?.length ?? 0} of {hostsTotal} hosts
                            </span>
                            <div className="flex items-center gap-2">
                                <button
                                    onClick={() => onFetchPage(hostsPage - 1)}
                                    disabled={hostsPage <= 1}
                                    className="btn-secondary btn-sm disabled:opacity-50"
                                >
                                    <ChevronLeft size={16} />
                                    Previous
                                </button>
                                <span className="text-sm text-dark-400">
                                    Page {hostsPage} of {serverTotalPages}
                                </span>
                                <button
                                    onClick={() => onFetchPage(hostsPage + 1)}
                                    disabled={hostsPage >= serverTotalPages}
                                    className="btn-secondary btn-sm disabled:opacity-50"
                                >
                                    Next
                                    <ChevronRight size={16} />
                                </button>
                            </div>
                        </div>
                    )}

                </>
            )}
        </div>
    )
}

function ChecklistGroupCard({
    group,
    onSelectItem,
    onViewOutput,
    getStatusIcon,
    selectedItems,
    onToggleSelect,
    onStopExecution,
    onDeleteGroup,
    onDeleteItem,
    currentUserId,
    onForceTakeover,
    batchProgress,
}) {
    const [expanded, setExpanded] = useState(true)

    return (
        <div className="card">
            <div className="flex items-center justify-between pr-2">
                <button
                    onClick={() => setExpanded(!expanded)}
                    className="flex-1 flex items-center justify-between py-2"
                >
                    <div className="flex items-center gap-3">
                        {expanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                        <h3 className="font-semibold text-dark-100">{group.name}</h3>
                        {group.ad_domain_name && (
                            <span className="px-1.5 py-0.5 text-[10px] rounded bg-purple-500/15 text-purple-400">{group.ad_domain_name}</span>
                        )}
                        {group.speed_profile && group.speed_profile !== 'default' && (
                            <span className={`px-1.5 py-0.5 text-[10px] rounded ${group.speed_profile === 'stealth' ? 'bg-blue-500/15 text-blue-400' : 'bg-orange-500/15 text-orange-400'}`}>
                                {group.speed_profile === 'stealth' ? 'Stealth' : 'Fast'}
                            </span>
                        )}
                        <span className="badge-info">
                            {group.completion_stats?.completed}/{group.completion_stats?.total}
                        </span>
                    </div>
                </button>
                <div className="flex items-center gap-3">
                    <div className="progress-bar w-24 h-1.5">
                        <div className="progress-fill" style={{ width: `${group.completion_stats?.percentage || 0}%` }}></div>
                    </div>
                    <button
                        onClick={(e) => {
                            e.stopPropagation()
                            onDeleteGroup(group.id, group.name)
                        }}
                        className="p-1.5 text-dark-500 hover:text-accent-danger hover:bg-dark-800 rounded transition-colors"
                        title="Delete Group"
                    >
                        <Trash2 size={16} />
                    </button>
                </div>
            </div>

            {expanded && (
                <div className="mt-4 space-y-2">
                    {(group.items || []).map((item) => {
                        const claim = item.claim
                        const claimedByOther = Boolean(
                            claim?.is_active &&
                            claim?.claimed_by_user_id &&
                            claim.claimed_by_user_id !== currentUserId
                        )

                        return (
                            <div
                                key={item.id}
                                className={`flex items-center justify-between p-3 rounded-lg transition-colors group
                                ${selectedItems?.includes(item.id) ? 'bg-accent-primary/10 border border-accent-primary/30' : 'bg-dark-800/50 border border-transparent hover:bg-dark-800'}
                            `}
                            >
                                <div className="flex items-center gap-3">
                                    <input
                                        type="checkbox"
                                        className="checkbox"
                                        checked={selectedItems?.includes(item.id) || false}
                                        onChange={() => onToggleSelect(item.id)}
                                    />
                                    {getStatusIcon(item.latest_execution_status)}
                                    <div>
                                        <h4 className="text-dark-200 text-sm font-medium flex items-center gap-2">
                                            <span>{item.name}</span>
                                            {claim?.is_active && (
                                                <span className={`text-[10px] px-2 py-0.5 rounded-full border ${claimedByOther
                                                    ? 'text-accent-warning border-accent-warning/40 bg-accent-warning/10'
                                                    : 'text-accent-success border-accent-success/40 bg-accent-success/10'
                                                    }`}>
                                                    {claimedByOther
                                                        ? `claimed by ${claim.claimed_by_username || 'operator'}`
                                                        : 'claimed by you'}
                                                </span>
                                            )}
                                        </h4>
                                        {item.description && (
                                            <p className="text-xs text-dark-500 line-clamp-1">{item.description}</p>
                                        )}
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    {(() => {
                                        const bp = batchProgress?.[item.id]
                                        if (!bp) return null
                                        const bpRunning = bp.status === 'running'
                                        const allDone = bp.completed >= bp.total && bp.total > 0
                                        const hasFails = bp.completed > bp.success
                                        let badgeClass = 'text-blue-400 border-blue-400/40 bg-blue-400/10'
                                        if (!bpRunning && allDone && !hasFails) badgeClass = 'text-accent-success border-accent-success/40 bg-accent-success/10'
                                        else if (!bpRunning && hasFails) badgeClass = 'text-accent-warning border-accent-warning/40 bg-accent-warning/10'
                                        return (
                                            <span className={`text-[11px] font-mono font-semibold px-2 py-0.5 rounded-full border ${badgeClass}`} title={`${bp.success} successful / ${bp.completed} completed / ${bp.total} total`}>
                                                {bp.completed}/{bp.total}
                                            </span>
                                        )
                                    })()}
                                    {(() => {
                                        const bp = batchProgress?.[item.id]
                                        const batchRunning = bp && bp.status === 'running'
                                        const isRunning = batchRunning || item.latest_execution_status === 'running'

                                        if (isRunning) {
                                            const cancelId = batchRunning ? bp.executionId : item.latest_execution_id
                                            return (
                                                <div className="flex items-center gap-2">
                                                    <button onClick={() => onViewOutput(item)} className="btn-ghost btn-sm">
                                                        <Terminal size={14} />
                                                    </button>
                                                    <button
                                                        onClick={() => onStopExecution(cancelId, !!batchRunning)}
                                                        className="btn-danger btn-sm animate-pulse"
                                                        title={batchRunning ? 'Kill All (Batch)' : 'Stop Execution'}
                                                    >
                                                        <StopCircle size={14} />
                                                    </button>
                                                </div>
                                            )
                                        }

                                        return (
                                            <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                                <button onClick={() => onViewOutput(item)} className="btn-ghost btn-sm">
                                                    <Terminal size={14} />
                                                </button>
                                                {claimedByOther ? (
                                                    <button
                                                        onClick={() => onForceTakeover(item)}
                                                        className="btn-warning btn-sm"
                                                        title="Force Takeover"
                                                    >
                                                        <AlertCircle size={14} />
                                                    </button>
                                                ) : (
                                                    <>
                                                        <button onClick={() => onSelectItem(item, true)} className="btn-success btn-sm" title="Quick Run">
                                                            <Play size={14} />
                                                        </button>
                                                        <button onClick={() => onSelectItem(item, false)} className="btn-secondary btn-sm" title="Configure & Run">
                                                            <Settings2 size={14} />
                                                        </button>
                                                    </>
                                                )}
                                                <button
                                                    onClick={() => onDeleteItem(item.id, item.name)}
                                                    className="btn-danger btn-sm bg-transparent border-transparent hover:bg-dark-700 text-dark-400 hover:text-accent-danger"
                                                    title="Delete Item"
                                                >
                                                    <Trash2 size={14} />
                                                </button>
                                            </div>
                                        )
                                    })()}
                                </div>
                            </div>
                        )
                    })}
                </div>
            )}
        </div>
    )
}

function ImportModal({ type, projectId, onClose, onSuccess }) {
    const [templates, setTemplates] = useState([])
    const [selectedIds, setSelectedIds] = useState([])
    const [loading, setLoading] = useState(true)
    const [adDomains, setAdDomains] = useState([])
    const [targetDomainId, setTargetDomainId] = useState('')

    useEffect(() => {
        const fetchTemplates = async () => {
            try {
                const api = type === 'checklist' ? checklistsApi : flowsApi
                const { data } = await (type === 'checklist' ? api.listGroups(null, true) : api.list(null, true))
                setTemplates(data)
            } catch (error) {
                toast.error('Failed to load templates')
            } finally {
                setLoading(false)
            }
        }
        fetchTemplates()
        adDomainsApi.list(projectId).then(({ data }) => setAdDomains(data || [])).catch(() => {})
    }, [type, projectId])

    const handleImport = async () => {
        if (selectedIds.length === 0) return
        try {
            const api = type === 'checklist' ? checklistsApi : flowsApi
            const domainId = targetDomainId ? parseInt(targetDomainId) : undefined
            await api.import(projectId, selectedIds, domainId)
            toast.success(`Imported ${selectedIds.length} templates`)
            onSuccess()
            onClose()
        } catch (error) {
            toast.error('Failed to import templates')
        }
    }


    const toggleSelect = (id) => {
        if (selectedIds.includes(id)) {
            setSelectedIds(selectedIds.filter(i => i !== id))
        } else {
            setSelectedIds([...selectedIds, id])
        }
    }

    return (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
            <div className="card max-w-lg w-full animate-slide-in flex flex-col max-h-[80vh]">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-bold text-dark-50">
                        Import {type === 'checklist' ? 'Checklists' : 'Flows'}
                    </h2>
                    <button onClick={onClose} className="btn-ghost btn-sm">✕</button>
                </div>

                {adDomains.length > 0 && (
                    <div className="mb-4">
                        <label className="block text-sm text-dark-400 mb-1">Target AD Domain (optional)</label>
                        <select
                            className="input-field bg-dark-900 border-dark-700 text-sm w-full"
                            value={targetDomainId}
                            onChange={e => setTargetDomainId(e.target.value)}
                        >
                            <option value="">No domain (global)</option>
                            {adDomains.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                        </select>
                        <p className="text-xs text-dark-500 mt-1">Imported checklists will be scoped to this domain.</p>
                    </div>
                )}

                <div className="flex-1 overflow-y-auto space-y-2 mb-4 pr-2">
                    {loading ? (
                        <div className="text-center py-8"><Loader2 className="animate-spin mx-auto" /></div>
                    ) : templates.length === 0 ? (
                        <div className="text-center py-8 text-dark-400">No templates found in Library</div>
                    ) : (
                        templates.map(tmpl => (
                            <div
                                key={tmpl.id}
                                onClick={() => toggleSelect(tmpl.id)}
                                className={`
                                    p-3 rounded-lg border cursor-pointer transition-all flex items-start gap-3
                                    ${selectedIds.includes(tmpl.id)
                                        ? 'bg-accent-primary/10 border-accent-primary'
                                        : 'bg-dark-800 border-transparent hover:border-dark-600'}
                                `}
                            >
                                <div className={`
                                    w-5 h-5 rounded border flex items-center justify-center mt-0.5
                                    ${selectedIds.includes(tmpl.id)
                                        ? 'bg-accent-primary border-accent-primary'
                                        : 'border-dark-500'}
                                `}>
                                    {selectedIds.includes(tmpl.id) && <CheckCircle2 size={14} className="text-white" />}
                                </div>
                                <div>
                                    <h3 className="font-semibold text-dark-100">{tmpl.name}</h3>
                                    <p className="text-xs text-dark-400">{tmpl.description}</p>
                                    <p className="text-xs text-dark-500 mt-1">
                                        {type === 'checklist' ? `${tmpl.items?.length || 0} items` : 'Flow template'}
                                    </p>
                                </div>
                            </div>
                        ))
                    )}
                </div>

                <div className="flex gap-3 pt-4 border-t border-dark-700">
                    <button onClick={onClose} className="btn-secondary flex-1">Cancel</button>
                    <button
                        onClick={handleImport}
                        disabled={selectedIds.length === 0}
                        className="btn-primary flex-1 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        Import Selected ({selectedIds.length})
                    </button>
                </div>
            </div>
        </div>
    )
}

function OutputModal({ executionId, itemId, title, onClose }) {
    const [output, setOutput] = useState('')
    const [loading, setLoading] = useState(true)
    const [selectedExecId, setSelectedExecId] = useState(executionId)
    const [executionHistory, setExecutionHistory] = useState([])
    const [isFullscreen, setIsFullscreen] = useState(false)

    const fetchHistory = useCallback(async () => {
        if (!itemId) return
        try {
            const { data } = await executionsApi.list({ item_id: itemId, per_page: 200 })
            setExecutionHistory(data.items || [])
        } catch {
            setExecutionHistory([])
        }
    }, [itemId])

    useEffect(() => { fetchHistory() }, [fetchHistory])

    const fetchOutput = useCallback(async (showSpinner = true) => {
        if (!selectedExecId) return
        if (showSpinner) setLoading(true)
        try {
            const { data } = await executionsApi.getOutput(selectedExecId)
            setOutput(data.output || 'No output')
        } catch {
            if (showSpinner) {
                toast.error("Failed to load output")
                setOutput("Error loading output")
            }
        } finally {
            if (showSpinner) setLoading(false)
        }
    }, [selectedExecId])

    useEffect(() => { fetchOutput() }, [fetchOutput])

    // While the selected execution is still running, keep pulling its output — the modal
    // used to freeze at whatever existed when it was opened.
    const selectedStatus = executionHistory.find(e => e.id === selectedExecId)?.status
    useEffect(() => {
        if (!selectedExecId) return
        if (selectedStatus && !['pending', 'running'].includes(selectedStatus)) return
        const interval = setInterval(() => { fetchOutput(false); fetchHistory() }, 2000)
        return () => clearInterval(interval)
    }, [selectedExecId, selectedStatus, fetchOutput, fetchHistory])

    const handleDeleteExecution = async (execId) => {
        try {
            await executionsApi.delete(execId)
            const updated = executionHistory.filter(e => e.id !== execId)
            setExecutionHistory(updated)
            if (execId === selectedExecId) {
                if (updated.length > 0) {
                    setSelectedExecId(updated[0].id)
                } else {
                    setOutput('No executions remaining')
                    setSelectedExecId(null)
                }
            }
            toast.success('Execution deleted')
        } catch {
            toast.error('Failed to delete execution')
        }
    }

    return (
        <div className={`fixed inset-0 bg-black/60 flex items-center justify-center z-50 ${isFullscreen ? 'p-0' : 'p-4'}`}>
            <div className={`card flex flex-col animate-slide-in ${isFullscreen ? 'w-full h-full max-w-none rounded-none' : 'max-w-4xl w-full h-[80vh]'}`}>
                <div className="flex items-center justify-between mb-4 border-b border-dark-700 pb-3">
                    <h2 className="text-lg font-bold text-dark-50 flex items-center gap-2">
                        <Terminal size={18} />
                        {title}
                    </h2>
                    <button onClick={onClose} className="btn-ghost btn-sm">✕</button>
                </div>

                <div className="flex-1 overflow-hidden bg-dark-950 rounded-lg border border-dark-800 relative flex flex-col">
                    {loading ? (
                        <div className="absolute inset-0 flex items-center justify-center">
                            <Loader2 className="animate-spin text-accent-primary" size={32} />
                        </div>
                    ) : (
                        <OutputViewer
                            content={output}
                            command={executionHistory.find(e => e.id === selectedExecId)?.command}
                            className="h-full"
                            executionHistory={executionHistory}
                            selectedExecutionId={selectedExecId}
                            onSelectExecution={setSelectedExecId}
                            onDeleteExecution={handleDeleteExecution}
                            isFullscreen={isFullscreen}
                            onToggleFullscreen={() => setIsFullscreen(f => !f)}
                        />
                    )}
                </div>

                {!isFullscreen && (
                    <div className="flex justify-end pt-4">
                        <button onClick={onClose} className="btn-secondary">Close</button>
                    </div>
                )}
            </div>
        </div>
    )
}

function DeleteConfirmationModal({ name, type, onClose, onConfirm }) {
    return (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
            <div className="card max-w-md w-full animate-slide-in">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-bold text-dark-50">Delete {type === 'group' ? 'Group' : 'Item'}</h2>
                    <button onClick={onClose} className="btn-ghost btn-sm">✕</button>
                </div>

                <div className="py-4">
                    <p className="text-dark-200">
                        Are you sure you want to delete <span className="font-semibold text-white">{name}</span>?
                    </p>
                    <p className="text-sm text-dark-400 mt-2">
                        You can choose to keeping the execution results (Soft Delete) or delete everything (Hard Delete).
                    </p>
                </div>

                <div className="flex flex-col gap-3 mt-4">
                    <button
                        onClick={() => onConfirm(true)}
                        className="btn-secondary w-full justify-center"
                    >
                        Delete {type === 'group' ? 'Group' : 'Item'} Only (Keep Results)
                    </button>

                    <button
                        onClick={() => onConfirm(false)}
                        className="btn-danger w-full justify-center"
                    >
                        Delete with Results (Permanent)
                    </button>

                    <button onClick={onClose} className="btn-ghost w-full justify-center text-dark-400">
                        Cancel
                    </button>
                </div>
            </div>
        </div>
    )
}
