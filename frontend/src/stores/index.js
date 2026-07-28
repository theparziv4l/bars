import { create } from 'zustand'
import {
    projectsApi,
    hostsApi,
    checklistsApi,
    executionsApi,
    authApi,
    findingsApi,
    AUTH_STORAGE_KEY,
    setAuthToken,
} from '../services/api'

// Global app store
export const useAppStore = create((set, get) => ({
    // UI state
    sidebarOpen: true,
    toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),

    // Current project
    currentProject: null,
    setCurrentProject: (project) => set({ currentProject: project }),

    // Notifications
    notifications: [],
    addNotification: (notification) => set((state) => ({
        notifications: [...state.notifications, { id: Date.now(), ...notification }]
    })),
    removeNotification: (id) => set((state) => ({
        notifications: state.notifications.filter(n => n.id !== id)
    })),

    // WebSocket connection status
    wsConnected: false,
    setWsConnected: (connected) => set({ wsConnected: connected }),
}))

// Auth store
export const useAuthStore = create((set, get) => ({
    user: null,
    token: null,
    initialized: false,
    loading: false,
    error: null,

    isAuthenticated: () => {
        const state = get()
        return Boolean(state.token && state.user)
    },

    initialize: async () => {
        if (typeof window === 'undefined') {
            set({ initialized: true })
            return
        }
        const savedToken = window.localStorage.getItem(AUTH_STORAGE_KEY)
        if (!savedToken) {
            set({ initialized: true, token: null, user: null })
            return
        }

        setAuthToken(savedToken)
        try {
            const { data } = await authApi.me()
            set({ token: savedToken, user: data, initialized: true, error: null })
        } catch (error) {
            window.localStorage.removeItem(AUTH_STORAGE_KEY)
            setAuthToken(null)
            set({ token: null, user: null, initialized: true, error: null })
        }
    },

    login: async (username, password) => {
        set({ loading: true, error: null })
        try {
            const { data } = await authApi.login({ username, password })
            if (typeof window !== 'undefined') {
                window.localStorage.setItem(AUTH_STORAGE_KEY, data.access_token)
            }
            setAuthToken(data.access_token)
            set({ token: data.access_token, user: data.user, loading: false, initialized: true })
            return data
        } catch (error) {
            set({ loading: false, error: error.response?.data?.detail || 'Login failed' })
            throw error
        }
    },

    changePassword: async (currentPassword, newPassword) => {
        set({ loading: true, error: null })
        try {
            const { data } = await authApi.changePassword({
                current_password: currentPassword,
                new_password: newPassword,
            })
            set({ user: data, loading: false, error: null })
            return data
        } catch (error) {
            set({ loading: false, error: error.response?.data?.detail || 'Password change failed' })
            throw error
        }
    },

    setUser: (user) => set({ user }),

    logout: () => {
        if (typeof window !== 'undefined') {
            window.localStorage.removeItem(AUTH_STORAGE_KEY)
        }
        setAuthToken(null)
        set({ token: null, user: null, error: null })
    },
}))

// Projects store
export const useProjectsStore = create((set, get) => ({
    projects: [],
    loading: false,
    error: null,

    fetchProjects: async (params = {}) => {
        set({ loading: true, error: null })
        try {
            const { data } = await projectsApi.list(params)
            set({ projects: data.items, loading: false })
            return data
        } catch (error) {
            set({ error: error.message, loading: false })
            throw error
        }
    },

    createProject: async (projectData) => {
        const { data } = await projectsApi.create(projectData)
        // Refetch rather than prepend: the list is server-sorted and paginated.
        await get().fetchProjects()
        return data
    },

    updateProject: async (id, projectData) => {
        const { data } = await projectsApi.update(id, projectData)
        set((state) => ({
            projects: state.projects.map(p => p.id === id ? data : p)
        }))
        return data
    },

    deleteProject: async (id) => {
        await projectsApi.delete(id)
        set((state) => ({
            projects: state.projects.filter(p => p.id !== id)
        }))
    },

    // Variables state
    projectVariables: [],
    variablesProjectId: null,
    fetchProjectVariables: async (projectId) => {
        set({ projectVariables: [], variablesProjectId: projectId })
        try {
            const { data } = await projectsApi.getVariables(projectId)
            set({ projectVariables: data })
        } catch (error) {
            console.error(error)
        }
    },

    migrateOrphans: async () => {
        const { data } = await projectsApi.migrateOrphans()
        if (data.migrated_count > 0) {
            await get().fetchProjects()
        }
        return data
    },
}))

// Hosts store
export const useHostsStore = create((set, get) => ({
    hosts: [],
    hostsTotal: 0,
    hostsPage: 1,
    hostsPerPage: 100,
    hostsSortBy: null,
    hostsSortOrder: 'desc',
    hostsTagsFilter: [],
    hostsSmbSigningFilter: null,
    hostsOsFilter: null,
    hostsPortSearch: null,
    hostsDomainFilter: null,
    currentHost: null,
    currentProjectId: null,
    hostsParams: { per_page: 100, page: 1 },
    loading: false,

    setHostsSort: (sortBy, sortOrder) => set({ hostsSortBy: sortBy, hostsSortOrder: sortOrder }),
    setHostsTagsFilter: (tags) => set({ hostsTagsFilter: Array.isArray(tags) ? tags : [] }),
    setHostsSmbSigningFilter: (val) => set({ hostsSmbSigningFilter: val || null }),
    setHostsOsFilter: (val) => set({ hostsOsFilter: val || null }),
    setHostsPortSearch: (val) => set({ hostsPortSearch: val != null && val !== '' ? Number(val) : null }),
    setHostsDomainFilter: (val) => set({ hostsDomainFilter: val || null }),

    fetchHosts: async (projectId, params = {}) => {
        set({ loading: true, currentProjectId: projectId, hostsParams: params })
        const { hostsSortBy, hostsSortOrder, hostsTagsFilter, hostsSmbSigningFilter, hostsOsFilter, hostsPortSearch, hostsDomainFilter } = get()
        const mergedParams = {
            ...params,
            ...(hostsSortBy != null && { sort_by: hostsSortBy, sort_order: params.sort_order ?? hostsSortOrder }),
            ...(hostsTagsFilter?.length > 0 && { tags: hostsTagsFilter }),
            ...(hostsSmbSigningFilter && { smb_signing: hostsSmbSigningFilter }),
            ...(hostsOsFilter && { os_info: hostsOsFilter }),
            ...(hostsPortSearch != null && { port: hostsPortSearch }),
            ...(hostsDomainFilter && { domain: hostsDomainFilter }),
        }
        try {
            const { data } = await hostsApi.list(projectId, mergedParams)
            set({
                hosts: data.items,
                hostsTotal: data.total ?? data.items?.length ?? 0,
                hostsPage: data.page ?? 1,
                hostsPerPage: data.per_page ?? 100,
                loading: false,
            })
            return data
        } catch (error) {
            set({ loading: false })
            throw error
        }
    },

    // Re-run the last fetch with the operator's page/search/filters intact. A background
    // discovery event must not silently reset the Assets view to page 1, unfiltered.
    refetchHosts: async () => {
        const { currentProjectId, hostsParams, fetchHosts } = get()
        if (currentProjectId == null) return
        return fetchHosts(currentProjectId, hostsParams)
    },

    fetchHost: async (id) => {
        const { data } = await hostsApi.get(id)
        set({ currentHost: data })
        return data
    },

    createHost: async (hostData) => {
        const { data } = await hostsApi.create(hostData)
        // Refetch instead of prepending: `hosts` is a server-sorted, server-filtered,
        // paginated slice and `hostsTotal` would go stale.
        await get().refetchHosts()
        return data
    },
}))

// Keep a group's "X/Y" badge and progress bar consistent with its own item list.
// Any local mutation of group.items must go through this, or the denominator drifts
// until the page is reloaded.
function withCompletionStats(group) {
    const items = group.items || []
    const total = items.length
    const completed = items.filter(i =>
        i.latest_execution_status === 'completed' ||
        (i.latest_execution_status === undefined && i.has_successful_execution)
    ).length
    return {
        ...group,
        completion_stats: {
            ...group.completion_stats,
            completed,
            total,
            percentage: total > 0 ? Math.round((completed / total) * 100) : 0,
        },
    }
}

// Checklists store
export const useChecklistsStore = create((set, get) => ({
    groups: [],
    loading: false,
    batchProgress: {},
    currentProjectId: null,

    updateBatchProgress: (itemId, progressData) => {
        set((state) => ({
            batchProgress: {
                ...state.batchProgress,
                [itemId]: { ...(state.batchProgress[itemId] || {}), ...progressData },
            },
        }))
    },

    clearBatchProgress: (itemId) => {
        set((state) => {
            const next = { ...state.batchProgress }
            delete next[itemId]
            return { batchProgress: next }
        })
    },

    fetchGroups: async (projectId) => {
        set({ loading: true, currentProjectId: projectId })
        try {
            const { data } = await checklistsApi.listGroups(projectId)
            set({ groups: data, loading: false })
            return data
        } catch (error) {
            set({ loading: false })
            throw error
        }
    },

    createGroup: async (groupData) => {
        const { data } = await checklistsApi.createGroup(groupData)
        set((state) => ({ groups: [...state.groups, data] }))
        return data
    },

    createItem: async (itemData) => {
        const { data } = await checklistsApi.createItem(itemData)
        set((state) => ({
            groups: state.groups.map(g =>
                g.id === itemData.group_id
                    ? withCompletionStats({ ...g, items: [...(g.items || []), data] })
                    : g
            )
        }))
        return data
    },

    updateItemStatus: (itemId, status, executionId = null) => {
        set((state) => ({
            groups: state.groups.map(g => {
                const itemInGroup = g.items?.find(i => i.id === itemId)
                if (!itemInGroup) return g

                const updatedItems = g.items.map(i =>
                    i.id === itemId ? {
                        ...i,
                        latest_execution_status: status,
                        latest_execution_id: executionId || i.latest_execution_id,
                        // Mark as successfully executed when completed
                        has_successful_execution: status === 'completed' ? true : i.has_successful_execution
                    } : i
                )

                return withCompletionStats({ ...g, items: updatedItems })
            })
        }))
    },

    updateItemClaim: (itemId, claimData) => {
        set((state) => ({
            groups: state.groups.map((g) => ({
                ...g,
                items: (g.items || []).map((i) =>
                    i.id === itemId
                        ? {
                            ...i,
                            claim: claimData ? {
                                ...(i.claim || {}),
                                ...claimData,
                                is_active: claimData.is_active ?? true,
                            } : null,
                        }
                        : i
                ),
            })),
        }))
    },

    deleteGroup: async (id, softDelete = false) => {
        await checklistsApi.deleteGroup(id, softDelete)
        set((state) => ({
            groups: state.groups.filter(g => g.id !== id)
        }))
    },

    deleteItem: async (itemId, softDelete = false) => {
        await checklistsApi.deleteItem(itemId, softDelete)
        set((state) => ({
            groups: state.groups.map(g => withCompletionStats({
                ...g,
                items: (g.items || []).filter(i => i.id !== itemId)
            }))
        }))
    },
}))

// Executions store
export const useExecutionsStore = create((set, get) => ({
    executions: [],
    runningExecutions: [],
    currentExecution: null,

    fetchExecutions: async (params = {}) => {
        const { data } = await executionsApi.list(params)
        set({ executions: data.items })
        return data
    },

    startExecution: async (executionData) => {
        const { data } = await executionsApi.start(executionData)
        set((state) => ({
            executions: [data, ...state.executions],
            runningExecutions: [...state.runningExecutions, data.id]
        }))
        return data
    },

    updateExecution: (execution) => {
        set((state) => ({
            executions: state.executions.map(e => e.id === execution.id ? execution : e),
            currentExecution: state.currentExecution?.id === execution.id ? execution : state.currentExecution
        }))
    },

    setCurrentExecution: (execution) => set({ currentExecution: execution }),

    removeFromRunning: (id) => set((state) => ({
        runningExecutions: state.runningExecutions.filter(eid => eid !== id)
    })),
}))

// Findings store
export const useFindingsStore = create((set, get) => ({
    findings: [],
    summary: null,
    loading: false,
    findingsProjectId: null,

    findingsParams: {},

    fetchFindings: async (projectId, params = {}) => {
        set({ loading: true, findingsProjectId: projectId, findingsParams: params })
        try {
            const [{ data: findings }, { data: summary }] = await Promise.all([
                findingsApi.list(projectId, params),
                findingsApi.summary(projectId),
            ])
            set({ findings, summary, loading: false })
            return findings
        } catch (error) {
            set({ loading: false })
            throw error
        }
    },

    // Re-run the last fetch with its filters intact — used after a mutation and on the
    // finding_created websocket event, so the list AND the severity summary stay in sync.
    refetchFindings: async () => {
        const { findingsProjectId, findingsParams, fetchFindings } = get()
        if (findingsProjectId == null) return
        return fetchFindings(findingsProjectId, findingsParams)
    },

    createFinding: async (data) => {
        const { data: created } = await findingsApi.create(data)
        await get().refetchFindings()
        return created
    },

    updateFinding: async (id, patch) => {
        const { data } = await findingsApi.update(id, patch)
        await get().refetchFindings()
        return data
    },

    deleteFinding: async (id) => {
        await findingsApi.delete(id)
        await get().refetchFindings()
    },
}))
