import { useState, useEffect } from 'react'
import { Plus, Trash2, Save, X, Edit2, Download, CheckCircle2, Loader2, Copy, Shield, Globe, Filter } from 'lucide-react'
import { projectsApi, libraryVariablesApi, adDomainsApi } from '../services/api'
import { useProjectsStore } from '../stores'
import toast from 'react-hot-toast'
import ADDomainManager from './ADDomainManager'

export default function ProjectVariables({ projectId }) {
    const { projectVariables, fetchProjectVariables } = useProjectsStore()
    const [loading, setLoading] = useState(false)
    const [isAdding, setIsAdding] = useState(false)
    const [newVar, setNewVar] = useState({ key: '', value: '', var_type: 'string', ad_domain_id: null })
    const [editingId, setEditingId] = useState(null)
    const [editValue, setEditValue] = useState('')
    const [editType, setEditType] = useState('string')
    const [editBaseline, setEditBaseline] = useState('')  // value as it was when editing started
    const [showImportModal, setShowImportModal] = useState(false)
    const [adDomains, setAdDomains] = useState([])
    const [domainFilter, setDomainFilter] = useState('all') // 'all' | 'global' | domain_id

    const fetchDomains = async () => {
        try {
            const { data } = await adDomainsApi.list(projectId)
            setAdDomains(data)
        } catch { setAdDomains([]) }
    }

    useEffect(() => {
        fetchProjectVariables(projectId)
        fetchDomains()
    }, [projectId])

    const handleSave = async () => {
        if (!newVar.key) return
        try {
            const payload = { key: newVar.key, value: newVar.value, var_type: newVar.var_type }
            if (newVar.ad_domain_id) payload.ad_domain_id = newVar.ad_domain_id
            await projectsApi.createVariable(projectId, payload)
            toast.success("Variable saved")
            setNewVar({ key: '', value: '', var_type: 'string', ad_domain_id: null })
            setIsAdding(false)
            fetchProjectVariables(projectId)
        } catch (error) {
            toast.error("Failed to save variable")
        }
    }

    const handleDelete = async (id) => {
        if (!confirm("Are you sure you want to delete this variable?")) return
        try {
            await projectsApi.deleteVariable(projectId, id)
            toast.success("Variable deleted")
            fetchProjectVariables(projectId)
        } catch (error) {
            toast.error("Failed to delete variable")
        }
    }

    const startEdit = (v) => {
        setEditingId(v.id)
        setEditValue(typeof v.value === 'object' ? JSON.stringify(v.value, null, 2) : v.value)
        setEditType(v.var_type || 'string')
        setEditBaseline(typeof v.value === 'object' ? JSON.stringify(v.value, null, 2) : v.value)
    }

    const cancelEdit = () => {
        setEditingId(null)
        setEditValue('')
    }

    const saveEdit = async (v) => {
        try {
            // Another operator (or an execution's variable injection) may have changed this
            // value while the textarea was open; overwriting blind would silently revert it.
            const { data: fresh } = await projectsApi.getVariables(projectId)
            const server = (fresh || []).find(x => x.id === v.id)
            const serverValue = server && typeof server.value === 'object'
                ? JSON.stringify(server.value, null, 2)
                : server?.value
            if (server && serverValue !== editBaseline) {
                const proceed = confirm(
                    `This variable changed since you opened it.\n\nCurrent value:\n${serverValue}\n\nOverwrite with yours?`
                )
                if (!proceed) {
                    setEditingId(null)
                    fetchProjectVariables(projectId)
                    return
                }
            }

            const payload = { key: v.key, value: editValue, var_type: editType }
            if (v.ad_domain_id) payload.ad_domain_id = v.ad_domain_id
            await projectsApi.createVariable(projectId, payload)
            toast.success("Variable updated")
            setEditingId(null)
            fetchProjectVariables(projectId)
        } catch (error) {
            toast.error("Failed to update variable")
        }
    }

    const filteredVariables = projectVariables.filter(v => {
        if (domainFilter === 'all') return true
        if (domainFilter === 'global') return !v.ad_domain_id
        return v.ad_domain_id === parseInt(domainFilter)
    })

    const getDomainName = (domainId) => {
        if (!domainId) return null
        return adDomains.find(d => d.id === domainId)?.name || 'Unknown'
    }

    const groupedByDomain = () => {
        const groups = {}
        const globalVars = filteredVariables.filter(v => !v.ad_domain_id)
        if (globalVars.length > 0 || domainFilter === 'all' || domainFilter === 'global') {
            groups['global'] = { name: 'Global', vars: globalVars }
        }
        for (const d of adDomains) {
            const vars = filteredVariables.filter(v => v.ad_domain_id === d.id)
            if (vars.length > 0 || domainFilter === String(d.id)) {
                groups[d.id] = { name: d.name, vars }
            }
        }
        return groups
    }

    const hasMultipleDomains = adDomains.length > 0

    return (
        <div className="space-y-6">
            {/* AD Domain Manager */}
            <ADDomainManager
                projectId={projectId}
                domains={adDomains}
                onDomainsChange={fetchDomains}
            />

            {/* Variables Section */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-xl font-semibold text-dark-50">Project Variables</h2>
                    <p className="text-dark-400 text-sm">Define variables scoped to this project{hasMultipleDomains ? ' or specific AD domains' : ''}.</p>
                </div>
                <div className="flex gap-2">
                    <button onClick={() => setShowImportModal(true)} className="btn-secondary">
                        <Download size={18} /> Import from Library
                    </button>
                    <button onClick={() => setIsAdding(true)} className="btn-primary">
                        <Plus size={18} /> Add Variable
                    </button>
                </div>
            </div>

            {/* Domain filter tabs */}
            {hasMultipleDomains && (
                <div className="flex items-center gap-2 flex-wrap">
                    <Filter size={14} className="text-dark-500" />
                    <button
                        onClick={() => setDomainFilter('all')}
                        className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                            domainFilter === 'all' ? 'bg-accent-primary/20 text-accent-primary' : 'bg-dark-800 text-dark-400 hover:bg-dark-700'
                        }`}
                    >All</button>
                    <button
                        onClick={() => setDomainFilter('global')}
                        className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1 ${
                            domainFilter === 'global' ? 'bg-accent-primary/20 text-accent-primary' : 'bg-dark-800 text-dark-400 hover:bg-dark-700'
                        }`}
                    ><Globe size={12} /> Global</button>
                    {adDomains.map(d => (
                        <button
                            key={d.id}
                            onClick={() => setDomainFilter(String(d.id))}
                            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1 ${
                                domainFilter === String(d.id) ? 'bg-purple-500/20 text-purple-400' : 'bg-dark-800 text-dark-400 hover:bg-dark-700'
                            }`}
                        ><Shield size={12} /> {d.name}</button>
                    ))}
                </div>
            )}

            <div className="overflow-hidden bg-dark-800 rounded-xl border border-dark-700">
                <table className="w-full text-left text-sm">
                    <thead className="bg-dark-900 text-dark-400">
                        <tr>
                            <th className="px-6 py-3 font-medium">Key</th>
                            {hasMultipleDomains && <th className="px-6 py-3 font-medium">Domain</th>}
                            <th className="px-6 py-3 font-medium">Type</th>
                            <th className="px-6 py-3 font-medium">Value</th>
                            <th className="px-6 py-3 font-medium text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-dark-700">
                        {isAdding && (
                            <tr className="bg-dark-800/50">
                                <td className="px-6 py-3">
                                    <input
                                        autoFocus
                                        className="input-field w-full bg-dark-900 text-dark-100 placeholder-dark-500 border-dark-700 h-8 text-sm"
                                        placeholder="Variable Name"
                                        value={newVar.key}
                                        onChange={e => setNewVar({ ...newVar, key: e.target.value })}
                                    />
                                </td>
                                {hasMultipleDomains && (
                                    <td className="px-6 py-3">
                                        <select
                                            className="input-field bg-dark-900 border-dark-700 h-8 text-xs w-36"
                                            value={newVar.ad_domain_id || ''}
                                            onChange={e => setNewVar({ ...newVar, ad_domain_id: e.target.value ? parseInt(e.target.value) : null })}
                                        >
                                            <option value="">Global</option>
                                            {adDomains.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                                        </select>
                                    </td>
                                )}
                                <td className="px-6 py-3">
                                    <select
                                        className="input-field bg-dark-900 border-dark-700 h-8 text-xs w-24"
                                        value={newVar.var_type}
                                        onChange={e => setNewVar({ ...newVar, var_type: e.target.value })}
                                    >
                                        <option value="string">String</option>
                                        <option value="file">File</option>
                                    </select>
                                </td>
                                <td className="px-6 py-3">
                                    <textarea
                                        className="input-field w-full bg-dark-900 text-dark-100 placeholder-dark-500 border-dark-700 h-8 text-sm py-1 min-h-[2rem] focus:min-h-[6rem] transition-all resize-y"
                                        placeholder="Value"
                                        value={newVar.value}
                                        onChange={e => setNewVar({ ...newVar, value: e.target.value })}
                                    />
                                </td>
                                <td className="px-6 py-3 text-right">
                                    <div className="flex justify-end gap-2">
                                        <button onClick={handleSave} className="text-accent-primary hover:text-accent-primary/80"><Save size={18} /></button>
                                        <button onClick={() => setIsAdding(false)} className="text-dark-400 hover:text-dark-200"><X size={18} /></button>
                                    </div>
                                </td>
                            </tr>
                        )}
                        {filteredVariables.map(v => (
                            <tr key={v.id} className="hover:bg-dark-700/50 transition-colors">
                                <td className="px-6 py-3 font-mono text-accent-primary">{v.key}</td>
                                {hasMultipleDomains && (
                                    <td className="px-6 py-3">
                                        {v.ad_domain_id ? (
                                            <span className="px-2 py-0.5 text-xs rounded bg-purple-500/15 text-purple-400">{getDomainName(v.ad_domain_id)}</span>
                                        ) : (
                                            <span className="px-2 py-0.5 text-xs rounded bg-dark-700 text-dark-400">Global</span>
                                        )}
                                    </td>
                                )}
                                <td className="px-6 py-3">
                                    {editingId === v.id ? (
                                        <select
                                            className="input-field bg-dark-900 border-dark-700 h-7 text-xs w-24"
                                            value={editType}
                                            onChange={e => setEditType(e.target.value)}
                                        >
                                            <option value="string">String</option>
                                            <option value="file">File</option>
                                        </select>
                                    ) : (
                                        <span className={`text-xs px-2 py-0.5 rounded ${v.var_type === 'file' ? 'bg-accent-info/20 text-accent-info' : 'bg-dark-800 text-dark-400'}`}>
                                            {v.var_type || 'string'}
                                        </span>
                                    )}
                                </td>
                                <td className="px-6 py-3 text-dark-200 max-w-xl break-all whitespace-pre-wrap">
                                    {editingId === v.id ? (
                                        <textarea
                                            className="input-field w-full bg-dark-900 border-dark-700 text-sm font-mono min-h-[4rem]"
                                            value={editValue}
                                            onChange={e => setEditValue(e.target.value)}
                                            autoFocus
                                        />
                                    ) : (
                                        <div className="relative group pr-8">
                                            {v.var_type === 'file' ? (
                                                <pre className="text-xs font-mono bg-dark-950 p-2 rounded max-h-24 overflow-y-auto w-full">
                                                    {typeof v.value === 'object' ? JSON.stringify(v.value, null, 2) : v.value}
                                                </pre>
                                            ) : (
                                                <div className="line-clamp-3" title={typeof v.value === 'object' ? JSON.stringify(v.value) : v.value}>
                                                    {typeof v.value === 'object' ? JSON.stringify(v.value) : v.value}
                                                </div>
                                            )}
                                            <button
                                                onClick={() => {
                                                    const valToCopy = typeof v.value === 'object' ? JSON.stringify(v.value, null, 2) : v.value
                                                    navigator.clipboard.writeText(valToCopy)
                                                    toast.success("Copied to clipboard")
                                                }}
                                                className="absolute top-0 right-0 p-1 text-dark-400 hover:text-white bg-dark-800/80 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                                                title="Copy full value"
                                            >
                                                <Copy size={14} />
                                            </button>
                                        </div>
                                    )}
                                </td>
                                <td className="px-6 py-3 text-right">
                                    <div className="flex justify-end gap-2">
                                        {editingId === v.id ? (
                                            <>
                                                <button onClick={() => saveEdit(v)} className="text-accent-success hover:text-accent-success/80"><Save size={16} /></button>
                                                <button onClick={cancelEdit} className="text-dark-400 hover:text-dark-200"><X size={16} /></button>
                                            </>
                                        ) : (
                                            <>
                                                <button onClick={() => startEdit(v)} className="text-dark-400 hover:text-accent-primary"><Edit2 size={16} /></button>
                                                <button onClick={() => handleDelete(v.id)} className="text-dark-400 hover:text-accent-danger"><Trash2 size={16} /></button>
                                            </>
                                        )}
                                    </div>
                                </td>
                            </tr>
                        ))}
                        {filteredVariables.length === 0 && !isAdding && (
                            <tr>
                                <td colSpan={hasMultipleDomains ? 5 : 4} className="px-6 py-8 text-center text-dark-500">
                                    {domainFilter !== 'all'
                                        ? 'No variables for this filter. Add one or switch filter.'
                                        : 'No variables defined. Add one or import from library.'}
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            {showImportModal && (
                <ImportVariablesModal
                    projectId={projectId}
                    adDomains={adDomains}
                    onClose={() => setShowImportModal(false)}
                    onSuccess={() => fetchProjectVariables(projectId)}
                />
            )}
        </div>
    )
}

function ImportVariablesModal({ projectId, adDomains = [], onClose, onSuccess }) {
    const [libraryVars, setLibraryVars] = useState([])
    const [selectedIds, setSelectedIds] = useState([])
    const [loading, setLoading] = useState(true)
    const [importing, setImporting] = useState(false)
    const [targetDomainId, setTargetDomainId] = useState('')

    useEffect(() => {
        const fetchLibraryVars = async () => {
            try {
                const { data } = await libraryVariablesApi.list()
                setLibraryVars(data)
            } catch (error) {
                toast.error('Failed to load library variables')
            } finally {
                setLoading(false)
            }
        }
        fetchLibraryVars()
    }, [])

    const handleImport = async () => {
        if (selectedIds.length === 0) return
        setImporting(true)
        try {
            const domainId = targetDomainId ? parseInt(targetDomainId) : undefined
            await libraryVariablesApi.import(projectId, selectedIds, domainId)
            toast.success(`Imported ${selectedIds.length} variables`)
            onSuccess()
            onClose()
        } catch (error) {
            toast.error('Failed to import variables')
        } finally {
            setImporting(false)
        }
    }

    const toggleSelect = (id) => {
        if (selectedIds.includes(id)) {
            setSelectedIds(selectedIds.filter(i => i !== id))
        } else {
            setSelectedIds([...selectedIds, id])
        }
    }

    const selectAll = () => {
        if (selectedIds.length === libraryVars.length) {
            setSelectedIds([])
        } else {
            setSelectedIds(libraryVars.map(v => v.id))
        }
    }

    return (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
            <div className="card max-w-lg w-full animate-slide-in flex flex-col max-h-[80vh]">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-bold text-dark-50">
                        Import Variables from Library
                    </h2>
                    <button onClick={onClose} className="btn-ghost btn-sm">✕</button>
                </div>

                {adDomains.length > 0 && (
                    <div className="mb-4">
                        <label className="block text-sm text-dark-400 mb-1">Import into domain (optional)</label>
                        <select
                            className="input-field bg-dark-900 border-dark-700 text-sm w-full"
                            value={targetDomainId}
                            onChange={e => setTargetDomainId(e.target.value)}
                        >
                            <option value="">Global (no domain)</option>
                            {adDomains.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                        </select>
                    </div>
                )}

                <div className="flex-1 overflow-y-auto space-y-2 mb-4 pr-2">
                    {loading ? (
                        <div className="text-center py-8"><Loader2 className="animate-spin mx-auto" /></div>
                    ) : libraryVars.length === 0 ? (
                        <div className="text-center py-8 text-dark-400">No variables found in Library. Create some in Library → Variables first.</div>
                    ) : (
                        <>
                            <div className="flex items-center justify-between mb-2">
                                <button onClick={selectAll} className="text-xs text-accent-primary hover:underline">
                                    {selectedIds.length === libraryVars.length ? 'Deselect All' : 'Select All'}
                                </button>
                                <span className="text-xs text-dark-500">{selectedIds.length} selected</span>
                            </div>
                            {libraryVars.map(v => (
                                <div
                                    key={v.id}
                                    onClick={() => toggleSelect(v.id)}
                                    className={`
                                        p-3 rounded-lg border cursor-pointer transition-all flex items-start gap-3
                                        ${selectedIds.includes(v.id)
                                            ? 'bg-accent-primary/10 border-accent-primary'
                                            : 'bg-dark-800 border-transparent hover:border-dark-600'}
                                    `}
                                >
                                    <div className={`
                                        w-5 h-5 rounded border flex items-center justify-center mt-0.5
                                        ${selectedIds.includes(v.id)
                                            ? 'bg-accent-primary border-accent-primary'
                                            : 'border-dark-500'}
                                    `}>
                                        {selectedIds.includes(v.id) && <CheckCircle2 size={14} className="text-white" />}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2">
                                            <h3 className="font-mono font-semibold text-accent-primary">{v.key}</h3>
                                            <span className={`text-xs px-2 py-0.5 rounded ${v.var_type === 'file' ? 'bg-accent-info/20 text-accent-info' : 'bg-dark-700 text-dark-400'}`}>
                                                {v.var_type || 'string'}
                                            </span>
                                        </div>
                                        <p className="text-xs text-dark-400 mt-1 truncate">
                                            {v.description || (typeof v.value === 'object' ? JSON.stringify(v.value) : v.value)}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </>
                    )}
                </div>

                <div className="flex gap-3 pt-4 border-t border-dark-700">
                    <button onClick={onClose} className="btn-secondary flex-1">Cancel</button>
                    <button
                        onClick={handleImport}
                        disabled={selectedIds.length === 0 || importing}
                        className="btn-primary flex-1 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {importing ? <Loader2 size={18} className="animate-spin" /> : null}
                        Import Selected ({selectedIds.length})
                    </button>
                </div>
            </div>
        </div>
    )
}
