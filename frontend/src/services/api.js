import axios from 'axios'

export const AUTH_STORAGE_KEY = 'ptf_auth_token'

const api = axios.create({
    baseURL: '/api/v1',
    headers: {
        'Content-Type': 'application/json',
    },
})

export function setAuthToken(token) {
    if (token) {
        api.defaults.headers.common.Authorization = `Bearer ${token}`
    } else {
        delete api.defaults.headers.common.Authorization
    }
}

if (typeof window !== 'undefined') {
    const saved = window.localStorage.getItem(AUTH_STORAGE_KEY)
    if (saved) {
        setAuthToken(saved)
    }
}

api.interceptors.response.use(
    (response) => response,
    (error) => {
        if (typeof window !== 'undefined' && error?.response?.status === 401) {
            window.localStorage.removeItem(AUTH_STORAGE_KEY)
            setAuthToken(null)
            // Reflect the logout in the auth store so RequireAuth redirects to /login,
            // instead of rendering a "logged-in" shell full of failing requests. Lazy
            // import avoids the circular dependency with stores/index.js.
            import('../stores').then((m) => m.useAuthStore.getState().logout()).catch(() => {})
        }
        return Promise.reject(error)
    }
)

export const authApi = {
    login: (data) => api.post('/auth/login', data),
    me: () => api.get('/auth/me'),
    changePassword: (data) => api.post('/auth/change-password', data),
}

// Users (admin only)
export const usersApi = {
    list: () => api.get('/users'),
    get: (id) => api.get(`/users/${id}`),
    create: (data) => api.post('/users', data),
    update: (id, data) => api.put(`/users/${id}`, data),
    delete: (id) => api.delete(`/users/${id}`),
    resetPassword: (id, data) => api.post(`/users/${id}/reset-password`, data),
}

// Projects
export const projectsApi = {
    list: (params) => api.get('/projects', { params }),
    get: (id) => api.get(`/projects/${id}`),
    create: (data) => api.post('/projects', data),
    update: (id, data) => api.put(`/projects/${id}`, data),
    delete: (id) => api.delete(`/projects/${id}`),
    exportServices: (id) => api.post(`/projects/${id}/export-services`),
    generateScriptScan: (id, data = {}) => api.post(`/projects/${id}/script-scan`, data),
    getVariables: (id, adDomainId) => api.get(`/projects/${id}/variables`, { params: adDomainId != null ? { ad_domain_id: adDomainId } : {} }),
    getHostTags: (id) => api.get(`/projects/${id}/host-tags`),
    getHostFilterOptions: (id) => api.get(`/projects/${id}/host-filter-options`),
    createVariable: (id, data) => api.post(`/projects/${id}/variables`, data),
    deleteVariable: (projectId, variableId) => api.delete(`/projects/${projectId}/variables/${variableId}`),
    getSmbCredentials: (id) => api.get(`/projects/${id}/smb-credentials`),
    saveSmbCredentials: (id, data) => api.put(`/projects/${id}/smb-credentials`, data),
    listMembers: (id) => api.get(`/projects/${id}/members`),
    addMember: (id, data) => api.post(`/projects/${id}/members`, data),
    removeMember: (projectId, userId) => api.delete(`/projects/${projectId}/members/${userId}`),
    migrateOrphans: () => api.post('/projects/migrate-orphans'),
}

// AD Domains
export const adDomainsApi = {
    list: (projectId) => api.get('/ad-domains', { params: { project_id: projectId } }),
    get: (id) => api.get(`/ad-domains/${id}`),
    create: (data) => api.post('/ad-domains', data),
    update: (id, data) => api.put(`/ad-domains/${id}`, data),
    delete: (id) => api.delete(`/ad-domains/${id}`),
}

// Serialize params so arrays become repeated keys (tags=foo&tags=bar) for FastAPI List query params
function serializeParamsRepeatedArrays(params) {
    return Object.entries(params)
        .filter(([, v]) => v != null && v !== '')
        .flatMap(([k, v]) =>
            Array.isArray(v)
                ? v.map((val) => `${encodeURIComponent(k)}=${encodeURIComponent(val)}`)
                : [`${encodeURIComponent(k)}=${encodeURIComponent(v)}`]
        )
        .join('&')
}

// Hosts
export const hostsApi = {
    list: (projectId, params) => {
        const allParams = { project_id: projectId, ...params }
        return api.get('/hosts', {
            params: allParams,
            paramsSerializer: (p) => serializeParamsRepeatedArrays(p),
        })
    },
    get: (id) => api.get(`/hosts/${id}`),
    create: (data) => api.post('/hosts', data),
    update: (id, data) => api.put(`/hosts/${id}`, data),
    delete: (id) => api.delete(`/hosts/${id}`),
    timeline: (id) => api.get(`/hosts/${id}/timeline`),
    addService: (hostId, data) => api.post(`/hosts/${hostId}/services`, data),
    scriptScan: (hostId, data = {}) => api.post(`/hosts/${hostId}/script-scan`, data),
}

// Checklists
export const checklistsApi = {
    listGroups: (projectId, isTemplate = false) => api.get('/checklists/groups', { params: { project_id: projectId, is_template: isTemplate } }),
    createGroup: (data) => api.post('/checklists/groups', data),
    updateGroup: (id, data) => api.put(`/checklists/groups/${id}`, data),
    deleteGroup: (id, softDelete = false) => api.delete(`/checklists/groups/${id}`, { params: { soft_delete: softDelete } }),
    listItems: (groupId) => api.get('/checklists/items', { params: { group_id: groupId } }),
    getItem: (id) => api.get(`/checklists/items/${id}`),
    createItem: (data) => api.post('/checklists/items', data),
    updateItem: (id, data) => api.put(`/checklists/items/${id}`, data),
    deleteItem: (id, softDelete = false) => api.delete(`/checklists/items/${id}`, { params: { soft_delete: softDelete } }),
    import: (projectId, templateGroupIds, adDomainId) => api.post('/checklists/import', templateGroupIds, { params: { project_id: projectId, ...(adDomainId != null ? { ad_domain_id: adDomainId } : {}) } }),
    importAll: (projectId, adDomainId) => api.post('/checklists/import/all', {}, { params: { project_id: projectId, ...(adDomainId != null ? { ad_domain_id: adDomainId } : {}) } }),
    export: (projectId, groupId) => api.get('/checklists/export/json', { params: { project_id: projectId, group_id: groupId }, responseType: 'blob' }),
    importFile: (projectId, file, adDomainId) => {
        const formData = new FormData();
        formData.append('file', file);
        return api.post('/checklists/import/file', formData, {
            params: { project_id: projectId, ...(adDomainId != null ? { ad_domain_id: adDomainId } : {}) },
            headers: { 'Content-Type': 'multipart/form-data' },
        });
    },
    claimItem: (itemId, data) => api.post(`/checklists/items/${itemId}/claim`, data),
    releaseItem: (itemId, data) => api.post(`/checklists/items/${itemId}/release`, data),
    takeoverItem: (itemId, data) => api.post(`/checklists/items/${itemId}/takeover`, data),
}

// Executions
export const executionsApi = {
    list: (params) => api.get('/executions', { params }),
    get: (id) => api.get(`/executions/${id}`),
    start: (data) => api.post('/executions', data),
    startBulk: (data) => api.post('/executions/bulk', data),
    cancel: (id) => api.post(`/executions/${id}/cancel`),
    cancelBatch: (id) => api.post(`/executions/${id}/cancel-batch`),
    getOutput: (id, stream = 'stdout') => api.get(`/executions/${id}/output`, { params: { stream } }),
    getRunning: () => api.get('/executions/running'),
    delete: (id) => api.delete(`/executions/${id}`),
}

// Flows
export const flowsApi = {
    list: (projectId, isTemplate = false) => api.get('/flows', { params: { project_id: projectId, is_template: isTemplate } }),
    get: (id) => api.get(`/flows/${id}`),
    create: (data) => api.post('/flows', data),
    update: (id, data) => api.put(`/flows/${id}`, data),
    delete: (id) => api.delete(`/flows/${id}`),
    execute: (id, data) => api.post(`/flows/${id}/execute`, data),
    getStatus: (id) => api.get(`/flows/${id}/status`),
    import: (projectId, templateFlowIds, adDomainId) => api.post('/flows/import', templateFlowIds, { params: { project_id: projectId, ...(adDomainId ? { ad_domain_id: adDomainId } : {}) } }),
    export: (projectId, flowId) => api.get('/flows/export/json', { params: { project_id: projectId, flow_id: flowId }, responseType: 'blob' }),
    importFile: (projectId, file, adDomainId) => {
        const formData = new FormData();
        formData.append('file', file);
        return api.post('/flows/import/file', formData, {
            params: { project_id: projectId, ...(adDomainId ? { ad_domain_id: adDomainId } : {}) },
            headers: { 'Content-Type': 'multipart/form-data' },
        });
    },
    stopExecution: (flowId, flowExecutionId) => api.post(`/flows/${flowId}/execution/${flowExecutionId}/stop`),
    pauseExecution: (flowId, flowExecutionId) => api.post(`/flows/${flowId}/execution/${flowExecutionId}/pause`),
    resumeExecution: (flowId, flowExecutionId) => api.post(`/flows/${flowId}/execution/${flowExecutionId}/resume`),
    listExecutions: (flowId) => api.get(`/flows/${flowId}/executions`),
    getStatusByExec: (flowId, flowExecutionId) => api.get(`/flows/${flowId}/status`, { params: { flow_execution_id: flowExecutionId } }),
}

// Files
export const filesApi = {
    list: (hostId, params) => api.get('/files', { params: { host_id: hostId, ...params } }),
    getTree: (hostId, shareName) => api.get('/files/tree', { params: { host_id: hostId, share_name: shareName } }),
    getShares: (hostId) => api.get('/files/shares', { params: { host_id: hostId } }),
    get: (id) => api.get(`/files/${id}`),
    fetch: (id, data) => api.post(`/files/${id}/fetch`, data || {}),
    serve: (id) => `/api/v1/files/${id}/serve`,
    upload: (data) => api.post('/files/upload', data),
    getContent: (id) => api.get(`/files/${id}/content`),
    resolveSmbCredentials: (hostId) => api.get('/files/resolve-smb-credentials', { params: { host_id: hostId } }),
    browseLocal: (path) => api.get('/files/browse-local', { params: { path } }),
    search: (projectId, query, fileType) => api.get('/files/search/global', { params: { project_id: projectId, query, file_type: fileType } }),
}

// Search
export const searchApi = {
    global: (projectId, query, params) => api.get('/search', { params: { project_id: projectId, query, ...params } }),
    logs: (projectId, query, params) => api.get('/search/logs', { params: { project_id: projectId, query, ...params } }),
}

// Reports
export const reportsApi = {
    generate: (projectId, params) => api.post('/reports/generate', null, { params: { project_id: projectId, ...params }, responseType: 'blob' }),
    list: () => api.get('/reports/list'),
    download: (filename) => api.get(`/reports/download/${filename}`, { responseType: 'blob' }),
}

// Findings / vulnerabilities
export const findingsApi = {
    list: (projectId, params) => api.get('/findings', { params: { project_id: projectId, ...params } }),
    summary: (projectId) => api.get('/findings/summary', { params: { project_id: projectId } }),
    get: (id) => api.get(`/findings/${id}`),
    create: (data) => api.post('/findings', data),
    update: (id, data) => api.put(`/findings/${id}`, data),
    delete: (id) => api.delete(`/findings/${id}`),
}

// Evidence (proof attached to findings)
export const evidenceApi = {
    list: (findingId) => api.get('/evidence', { params: { finding_id: findingId } }),
    uploadFile: (formData) => api.post('/evidence', formData, { headers: { 'Content-Type': 'multipart/form-data' } }),
    attachText: (data) => api.post('/evidence/text', data),
    serve: (id) => `/api/v1/evidence/${id}/serve`,
    delete: (id) => api.delete(`/evidence/${id}`),
}

// Library Variables
export const libraryVariablesApi = {
    list: () => api.get('/variables/library'),
    create: (data) => api.post('/variables/library', data),
    update: (id, data) => api.put(`/variables/library/${id}`, data),
    delete: (id) => api.delete(`/variables/library/${id}`),
    import: (projectId, variableIds, adDomainId) => api.post('/variables/import', variableIds, { params: { project_id: projectId, ...(adDomainId != null ? { ad_domain_id: adDomainId } : {}) } }),
    export: () => api.get('/variables/export/json'),
    importFile: (file) => {
        const formData = new FormData()
        formData.append('file', file)
        return api.post('/variables/import/file', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
    },
}

// Whole Library (all types bundled)
export const libraryApi = {
    export: () => api.get('/library/export'),
    importFile: (file) => {
        const formData = new FormData()
        formData.append('file', file)
        return api.post('/library/import', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
    },
}

// Sync peers (dynamic in-app collaboration)
export const syncPeersApi = {
    list: () => api.get('/sync/peers'),
    add: (data) => api.post('/sync/peers', data),
    delete: (id) => api.delete(`/sync/peers/${id}`),
    nodeInfo: () => api.get('/sync/node-info'),
    /** Trigger one full DB sync with all peers (pull + push). Optional projectId to sync only that project. */
    runSync: (projectId = null) => api.post('/sync/run', null, { params: projectId != null ? { project_id: projectId } : {} }),
}

export default api

