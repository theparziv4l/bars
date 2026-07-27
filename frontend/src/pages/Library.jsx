import { useState, useEffect } from 'react'
import { Plus, FolderPlus, Trash2, Edit2, Copy, FileText, Workflow, Download, Upload, Settings2, Save, X, Package, Shield, Globe, Zap, Turtle, Gauge } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { checklistsApi, flowsApi, libraryVariablesApi, libraryApi } from '../services/api'
import toast from 'react-hot-toast'

const SPEED_PROFILE_CONFIG = {
    stealth: { icon: Turtle, label: 'Stealth', color: 'text-blue-400', bg: 'bg-blue-500/10' },
    default: { icon: Gauge, label: 'Default', color: 'text-green-400', bg: 'bg-green-500/10' },
    fast: { icon: Zap, label: 'Fast', color: 'text-orange-400', bg: 'bg-orange-500/10' },
}

function SpeedProfileBadge({ profile }) {
    const cfg = SPEED_PROFILE_CONFIG[profile] || SPEED_PROFILE_CONFIG.default
    const Icon = cfg.icon
    return (
        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs ${cfg.color} ${cfg.bg}`}>
            <Icon size={10} />
            {cfg.label}
        </span>
    )
}

export default function Library() {
    const navigate = useNavigate()
    const [activeTab, setActiveTab] = useState('checklists') // 'checklists', 'flows', or 'variables'
    const [checklistTemplates, setChecklistTemplates] = useState([])
    const [flowTemplates, setFlowTemplates] = useState([])
    const [libraryVariables, setLibraryVariables] = useState([])
    const [loading, setLoading] = useState(true)

    // Variable form state
    const [isAddingVar, setIsAddingVar] = useState(false)
    const [newVar, setNewVar] = useState({ key: '', value: '', var_type: 'string', description: '' })
    const [editingVarId, setEditingVarId] = useState(null)
    const [editVar, setEditVar] = useState({ key: '', value: '', var_type: 'string', description: '' })

    useEffect(() => {
        fetchTemplates()
    }, [])

    const fetchTemplates = async () => {
        try {
            setLoading(true)
            const [checklists, flows, variables] = await Promise.all([
                checklistsApi.listGroups(null, true),
                flowsApi.list(null, true),
                libraryVariablesApi.list()
            ])
            setChecklistTemplates(checklists.data)
            setFlowTemplates(flows.data)
            setLibraryVariables(variables.data)
        } catch (error) {
            toast.error('Failed to load templates')
            console.error(error)
        } finally {
            setLoading(false)
        }
    }

    const handleCreateChecklist = async () => {
        const name = prompt('Enter template name:')
        if (!name) return

        try {
            await checklistsApi.createGroup({
                project_id: null,
                name,
                description: 'Global template',
                is_template: true
            })
            toast.success('Template created')
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to create template')
        }
    }

    const handleDeleteChecklist = async (id) => {
        if (!confirm('Are you sure you want to delete this template?')) return
        try {
            await checklistsApi.deleteGroup(id)
            toast.success('Template deleted')
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to delete template')
        }
    }

    const handleCreateFlowTemplate = async () => {
        const name = prompt('Enter flow template name:')
        if (!name) return

        try {
            await flowsApi.create({
                project_id: null,
                name,
                description: 'Global flow template',
                is_template: true,
                steps: []
            })
            toast.success('Flow template created')
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to create flow template')
        }
    }

    const handleDeleteFlowTemplate = async (id) => {
        if (!confirm('Are you sure you want to delete this flow template?')) return
        try {
            await flowsApi.delete(id)
            toast.success('Flow template deleted')
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to delete flow template')
        }
    }

    const handleExport = async (type, id) => {
        try {
            const api = type === 'checklist' ? checklistsApi : flowsApi
            const response = await api.export(null, id)

            const url = window.URL.createObjectURL(new Blob([response.data]));
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', `${type}_${id ? 'template_' + id : 'library_export'}_${new Date().toISOString().slice(0, 10)}.json`);
            document.body.appendChild(link);
            link.click();
            link.remove();

            toast.success('Template exported')
        } catch (error) {
            toast.error('Failed to export template')
            console.error(error)
        }
    }

    const handleFileUpload = async (e) => {
        const file = e.target.files[0]
        if (!file) return

        try {
            const api = activeTab === 'checklists' ? checklistsApi : flowsApi
            const res = await api.importFile(null, file)
            toast.success(`Imported ${res.data.count} templates`)
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to import file')
            console.error(error)
        }
        e.target.value = null
    }

    const handleVariableExport = async () => {
        try {
            const response = await libraryVariablesApi.export()
            const blob = new Blob([typeof response.data === 'string' ? response.data : JSON.stringify(response.data, null, 2)], { type: 'application/json' })
            const url = window.URL.createObjectURL(blob)
            const link = document.createElement('a')
            link.href = url
            link.setAttribute('download', `variables_library_export_${new Date().toISOString().slice(0, 10)}.json`)
            document.body.appendChild(link)
            link.click()
            link.remove()
            toast.success('Variables exported')
        } catch (error) {
            toast.error('Failed to export variables')
            console.error(error)
        }
    }

    const handleVariableFileUpload = async (e) => {
        const file = e.target.files[0]
        if (!file) return
        try {
            const res = await libraryVariablesApi.importFile(file)
            toast.success(`Imported ${res.data.count} variables`)
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to import variables')
            console.error(error)
        }
        e.target.value = null
    }

    const handleLibraryExport = async () => {
        try {
            const response = await libraryApi.export()
            const blob = new Blob([typeof response.data === 'string' ? response.data : JSON.stringify(response.data, null, 2)], { type: 'application/json' })
            const url = window.URL.createObjectURL(blob)
            const link = document.createElement('a')
            link.href = url
            link.setAttribute('download', `full_library_export_${new Date().toISOString().slice(0, 10)}.json`)
            document.body.appendChild(link)
            link.click()
            link.remove()
            toast.success('Library exported')
        } catch (error) {
            toast.error('Failed to export library')
            console.error(error)
        }
    }

    const handleLibraryFileUpload = async (e) => {
        const file = e.target.files[0]
        if (!file) return
        try {
            const res = await libraryApi.importFile(file)
            const c = res.data.counts || {}
            toast.success(`Imported ${c.checklists || 0} checklists, ${c.flows || 0} flows, ${c.variables || 0} variables`)
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to import library')
            console.error(error)
        }
        e.target.value = null
    }

    // Variable handlers
    const handleCreateVariable = async () => {
        if (!newVar.key) {
            toast.error('Variable key is required')
            return
        }
        try {
            await libraryVariablesApi.create(newVar)
            toast.success('Variable created')
            setNewVar({ key: '', value: '', var_type: 'string', description: '' })
            setIsAddingVar(false)
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to create variable')
        }
    }

    const handleUpdateVariable = async (id) => {
        try {
            await libraryVariablesApi.update(id, editVar)
            toast.success('Variable updated')
            setEditingVarId(null)
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to update variable')
        }
    }

    const handleDeleteVariable = async (id) => {
        if (!confirm('Are you sure you want to delete this variable?')) return
        try {
            await libraryVariablesApi.delete(id)
            toast.success('Variable deleted')
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to delete variable')
        }
    }

    const startEditVariable = (v) => {
        setEditingVarId(v.id)
        setEditVar({ key: v.key, value: v.value, var_type: v.var_type, description: v.description || '' })
    }

    const handleToggleChecklistFlag = async (tmpl, field, value) => {
        try {
            const newValue = value !== undefined ? value : !tmpl[field]
            await checklistsApi.updateGroup(tmpl.id, { [field]: newValue })
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to update template')
        }
    }

    const handleToggleFlowFlag = async (tmpl, field) => {
        try {
            await flowsApi.update(tmpl.id, { [field]: !tmpl[field] })
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to update template')
        }
    }

    const handleToggleVarDefault = async (v) => {
        try {
            await libraryVariablesApi.update(v.id, { is_default_import: !v.is_default_import })
            fetchTemplates()
        } catch (error) {
            toast.error('Failed to update variable')
        }
    }

    return (
        <div className="max-w-7xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-dark-50">Library</h1>
                    <p className="text-dark-400">Manage global templates for checklists, flows, and variables</p>
                </div>
                <div className="flex gap-2">
                    <label className="btn-secondary cursor-pointer">
                        <input type="file" className="hidden" accept=".json" onChange={handleLibraryFileUpload} />
                        <Upload size={18} />
                        Import Library
                    </label>
                    <button onClick={handleLibraryExport} className="btn-secondary">
                        <Package size={18} />
                        Export Library
                    </button>
                </div>
            </div>

            {/* Tabs */}
            <div className="flex border-b border-dark-700">
                <button
                    onClick={() => setActiveTab('checklists')}
                    className={`px-4 py-2 font-medium transition-colors border-b-2 ${activeTab === 'checklists'
                        ? 'border-accent-primary text-accent-primary'
                        : 'border-transparent text-dark-400 hover:text-dark-200'
                        }`}
                >
                    <div className="flex items-center gap-2">
                        <FileText size={18} />
                        Checklist Templates
                    </div>
                </button>
                <button
                    onClick={() => setActiveTab('flows')}
                    className={`px-4 py-2 font-medium transition-colors border-b-2 ${activeTab === 'flows'
                        ? 'border-accent-primary text-accent-primary'
                        : 'border-transparent text-dark-400 hover:text-dark-200'
                        }`}
                >
                    <div className="flex items-center gap-2">
                        <Workflow size={18} />
                        Flow Templates
                    </div>
                </button>
                <button
                    onClick={() => setActiveTab('variables')}
                    className={`px-4 py-2 font-medium transition-colors border-b-2 ${activeTab === 'variables'
                        ? 'border-accent-primary text-accent-primary'
                        : 'border-transparent text-dark-400 hover:text-dark-200'
                        }`}
                >
                    <div className="flex items-center gap-2">
                        <Settings2 size={18} />
                        Variables
                    </div>
                </button>
            </div>

            {loading ? (
                <div className="p-8 text-center text-dark-500">Loading templates...</div>
            ) : (
                <div className="space-y-4">
                    {/* Toolbar */}
                    <div className="flex justify-end">
                        {activeTab === 'checklists' && (
                            <div className="flex gap-2">
                                <button onClick={handleCreateChecklist} className="btn-primary">
                                    <Plus size={18} />
                                    Create Checklist Template
                                </button>
                                <label className="btn-secondary cursor-pointer">
                                    <input type="file" className="hidden" accept=".json" onChange={handleFileUpload} />
                                    <Upload size={18} />
                                    Import JSON
                                </label>
                                <button onClick={() => handleExport('checklist', null)} className="btn-secondary">
                                    <Download size={18} />
                                    Export All
                                </button>
                            </div>
                        )}
                        {activeTab === 'flows' && (
                            <div className="flex gap-2">
                                <button onClick={handleCreateFlowTemplate} className="btn-primary">
                                    <Plus size={18} />
                                    Create Flow Template
                                </button>
                                <label className="btn-secondary cursor-pointer">
                                    <input type="file" className="hidden" accept=".json" onChange={handleFileUpload} />
                                    <Upload size={18} />
                                    Import JSON
                                </label>
                                <button onClick={() => handleExport('flow', null)} className="btn-secondary">
                                    <Download size={18} />
                                    Export All
                                </button>
                            </div>
                        )}
                        {activeTab === 'variables' && (
                            <div className="flex gap-2">
                                <button onClick={() => setIsAddingVar(true)} className="btn-primary">
                                    <Plus size={18} />
                                    Add Variable
                                </button>
                                <label className="btn-secondary cursor-pointer">
                                    <input type="file" className="hidden" accept=".json" onChange={handleVariableFileUpload} />
                                    <Upload size={18} />
                                    Import JSON
                                </label>
                                <button onClick={handleVariableExport} className="btn-secondary">
                                    <Download size={18} />
                                    Export All
                                </button>
                            </div>
                        )}
                    </div>

                    {/* Content */}
                    {activeTab === 'checklists' && (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                            {checklistTemplates.map((tmpl) => (
                                <div
                                    key={tmpl.id}
                                    className="card group relative cursor-pointer hover:border-accent-primary transition-colors"
                                    onClick={() => navigate(`/library/checklist/${tmpl.id}`)}
                                >
                                    <div className="absolute top-4 right-4 flex gap-2">
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation()
                                                handleExport('checklist', tmpl.id)
                                            }}
                                            className="p-1.5 text-dark-400 hover:text-dark-100 bg-dark-800 rounded-lg mr-1"
                                            title="Export JSON"
                                        >
                                            <Download size={16} />
                                        </button>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation()
                                                handleDeleteChecklist(tmpl.id)
                                            }}
                                            className="p-1.5 text-dark-400 hover:text-accent-danger bg-dark-800 rounded-lg"
                                        >
                                            <Trash2 size={16} />
                                        </button>
                                    </div>
                                    <div className="flex items-start gap-4 mb-4">
                                        <div className="p-3 rounded-xl bg-accent-primary/10">
                                            <FileText size={24} className="text-accent-primary" />
                                        </div>
                                        <div>
                                            <h3 className="font-semibold text-dark-100">{tmpl.name}</h3>
                                            <p className="text-sm text-dark-400">{tmpl.description || 'No description'}</p>
                                        </div>
                                    </div>
                                    <div className="pt-4 border-t border-dark-700 space-y-2">
                                        <div className="flex justify-between text-sm text-dark-500">
                                            <span>{tmpl.items?.length || 0} items</span>
                                            <span>Global Template</span>
                                        </div>
                                        <div className="flex items-center gap-4" onClick={e => e.stopPropagation()}>
                                            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
                                                <input
                                                    type="checkbox"
                                                    checked={tmpl.is_default_import || false}
                                                    onChange={() => handleToggleChecklistFlag(tmpl, 'is_default_import')}
                                                    className="accent-accent-primary w-3.5 h-3.5"
                                                />
                                                <span className={tmpl.is_default_import ? 'text-accent-primary' : 'text-dark-500'}>Default</span>
                                            </label>
                                            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
                                                <input
                                                    type="checkbox"
                                                    checked={tmpl.requires_auth || false}
                                                    onChange={() => handleToggleChecklistFlag(tmpl, 'requires_auth')}
                                                    className="accent-purple-500 w-3.5 h-3.5"
                                                />
                                                <Shield size={12} className={tmpl.requires_auth ? 'text-purple-400' : 'text-dark-600'} />
                                                <span className={tmpl.requires_auth ? 'text-purple-400' : 'text-dark-500'}>Domain Based</span>
                                            </label>
                                            <select
                                                value={tmpl.speed_profile || 'default'}
                                                onChange={(e) => handleToggleChecklistFlag(tmpl, 'speed_profile', e.target.value)}
                                                className="text-xs bg-dark-800 border border-dark-600 rounded px-1.5 py-0.5 cursor-pointer"
                                            >
                                                <option value="stealth">Stealth</option>
                                                <option value="default">Default</option>
                                                <option value="fast">Fast</option>
                                            </select>
                                            <SpeedProfileBadge profile={tmpl.speed_profile} />
                                        </div>
                                    </div>
                                </div>
                            ))}
                            {checklistTemplates.length === 0 && (
                                <div className="col-span-full text-center py-12 text-dark-500">
                                    No checklist templates found. Create one to get started.
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'flows' && (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                            {flowTemplates.map((tmpl) => (
                                <div
                                    key={tmpl.id}
                                    className="card group relative cursor-pointer hover:border-accent-primary transition-colors"
                                    onClick={() => navigate(`/library/flow/${tmpl.id}`)}
                                >
                                    <div className="absolute top-4 right-4 flex gap-2">
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation()
                                                handleExport('flow', tmpl.id)
                                            }}
                                            className="p-1.5 text-dark-400 hover:text-dark-100 bg-dark-800 rounded-lg mr-1"
                                            title="Export JSON"
                                        >
                                            <Download size={16} />
                                        </button>
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation()
                                                handleDeleteFlowTemplate(tmpl.id)
                                            }}
                                            className="p-1.5 text-dark-400 hover:text-accent-danger bg-dark-800 rounded-lg"
                                            title="Delete flow template"
                                        >
                                            <Trash2 size={16} />
                                        </button>
                                    </div>
                                    <div className="flex items-start gap-4 mb-4">
                                        <div className="p-3 rounded-xl bg-accent-primary/10">
                                            <Workflow size={24} className="text-accent-primary" />
                                        </div>
                                        <div>
                                            <h3 className="font-semibold text-dark-100">{tmpl.name}</h3>
                                            <p className="text-sm text-dark-400">{tmpl.description || 'No description'}</p>
                                        </div>
                                    </div>
                                    <div className="pt-4 border-t border-dark-700 space-y-2">
                                        <div className="flex justify-between text-sm text-dark-500">
                                            <span>{tmpl.steps?.length || 0} steps</span>
                                            <span>Global Template</span>
                                        </div>
                                        <div className="flex items-center gap-4" onClick={e => e.stopPropagation()}>
                                            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
                                                <input
                                                    type="checkbox"
                                                    checked={tmpl.is_default_import || false}
                                                    onChange={() => handleToggleFlowFlag(tmpl, 'is_default_import')}
                                                    className="accent-accent-primary w-3.5 h-3.5"
                                                />
                                                <span className={tmpl.is_default_import ? 'text-accent-primary' : 'text-dark-500'}>Default</span>
                                            </label>
                                            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
                                                <input
                                                    type="checkbox"
                                                    checked={tmpl.requires_auth || false}
                                                    onChange={() => handleToggleFlowFlag(tmpl, 'requires_auth')}
                                                    className="accent-purple-500 w-3.5 h-3.5"
                                                />
                                                <Shield size={12} className={tmpl.requires_auth ? 'text-purple-400' : 'text-dark-600'} />
                                                <span className={tmpl.requires_auth ? 'text-purple-400' : 'text-dark-500'}>Domain Based</span>
                                            </label>
                                        </div>
                                    </div>
                                </div>
                            ))}
                            {flowTemplates.length === 0 && (
                                <div className="col-span-full text-center py-12 text-dark-500">
                                    No flow templates found. Create one to get started.
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'variables' && (
                        <div className="space-y-4">
                            <div className="overflow-hidden bg-dark-800 rounded-xl border border-dark-700">
                                <table className="w-full text-left text-sm">
                                    <thead className="bg-dark-900 text-dark-400">
                                        <tr>
                                            <th className="px-6 py-3 font-medium">Key</th>
                                            <th className="px-6 py-3 font-medium">Type</th>
                                            <th className="px-6 py-3 font-medium">Value</th>
                                            <th className="px-6 py-3 font-medium">Description</th>
                                            <th className="px-4 py-3 font-medium text-center">Default</th>
                                            <th className="px-6 py-3 font-medium text-right">Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-dark-700">
                                        {isAddingVar && (
                                            <tr className="bg-dark-800/50">
                                                <td className="px-6 py-3">
                                                    <input
                                                        autoFocus
                                                        className="input-field w-full bg-dark-900 text-dark-100 placeholder-dark-500 border-dark-700 h-8 text-sm"
                                                        placeholder="Variable Key"
                                                        value={newVar.key}
                                                        onChange={e => setNewVar({ ...newVar, key: e.target.value })}
                                                    />
                                                </td>
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
                                                        className="input-field w-full bg-dark-900 text-dark-100 placeholder-dark-500 border-dark-700 h-8 text-sm py-1 min-h-[2rem] resize-y"
                                                        placeholder="Value"
                                                        value={newVar.value}
                                                        onChange={e => setNewVar({ ...newVar, value: e.target.value })}
                                                    />
                                                </td>
                                                <td className="px-6 py-3">
                                                    <input
                                                        className="input-field w-full bg-dark-900 text-dark-100 placeholder-dark-500 border-dark-700 h-8 text-sm"
                                                        placeholder="Description"
                                                        value={newVar.description}
                                                        onChange={e => setNewVar({ ...newVar, description: e.target.value })}
                                                    />
                                                </td>
                                                <td className="px-4 py-3 text-center">-</td>
                                                <td className="px-6 py-3 text-right">
                                                    <div className="flex justify-end gap-2">
                                                        <button onClick={handleCreateVariable} className="text-accent-primary hover:text-accent-primary/80"><Save size={18} /></button>
                                                        <button onClick={() => setIsAddingVar(false)} className="text-dark-400 hover:text-dark-200"><X size={18} /></button>
                                                    </div>
                                                </td>
                                            </tr>
                                        )}
                                        {libraryVariables.map(v => (
                                            <tr key={v.id} className="hover:bg-dark-700/50 transition-colors">
                                                <td className="px-6 py-3 font-mono text-accent-primary">
                                                    {editingVarId === v.id ? (
                                                        <input
                                                            className="input-field w-full bg-dark-900 border-dark-700 h-8 text-sm font-mono"
                                                            value={editVar.key}
                                                            onChange={e => setEditVar({ ...editVar, key: e.target.value })}
                                                        />
                                                    ) : v.key}
                                                </td>
                                                <td className="px-6 py-3">
                                                    {editingVarId === v.id ? (
                                                        <select
                                                            className="input-field bg-dark-900 border-dark-700 h-7 text-xs w-24"
                                                            value={editVar.var_type}
                                                            onChange={e => setEditVar({ ...editVar, var_type: e.target.value })}
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
                                                <td className="px-6 py-3 text-dark-200">
                                                    {editingVarId === v.id ? (
                                                        <textarea
                                                            className="input-field w-full bg-dark-900 border-dark-700 text-sm font-mono min-h-[2rem]"
                                                            value={editVar.value}
                                                            onChange={e => setEditVar({ ...editVar, value: e.target.value })}
                                                        />
                                                    ) : (
                                                        <span className="line-clamp-2">{typeof v.value === 'object' ? JSON.stringify(v.value) : v.value}</span>
                                                    )}
                                                </td>
                                                <td className="px-6 py-3 text-dark-400 text-sm">
                                                    {editingVarId === v.id ? (
                                                        <input
                                                            className="input-field w-full bg-dark-900 border-dark-700 h-8 text-sm"
                                                            value={editVar.description}
                                                            onChange={e => setEditVar({ ...editVar, description: e.target.value })}
                                                        />
                                                    ) : (v.description || '-')}
                                                </td>
                                                <td className="px-4 py-3 text-center">
                                                    <input
                                                        type="checkbox"
                                                        checked={v.is_default_import || false}
                                                        onChange={() => handleToggleVarDefault(v)}
                                                        className="accent-accent-primary w-4 h-4 cursor-pointer"
                                                    />
                                                </td>
                                                <td className="px-6 py-3 text-right">
                                                    <div className="flex justify-end gap-2">
                                                        {editingVarId === v.id ? (
                                                            <>
                                                                <button onClick={() => handleUpdateVariable(v.id)} className="text-accent-success hover:text-accent-success/80"><Save size={16} /></button>
                                                                <button onClick={() => setEditingVarId(null)} className="text-dark-400 hover:text-dark-200"><X size={16} /></button>
                                                            </>
                                                        ) : (
                                                            <>
                                                                <button onClick={() => startEditVariable(v)} className="text-dark-400 hover:text-accent-primary"><Edit2 size={16} /></button>
                                                                <button onClick={() => handleDeleteVariable(v.id)} className="text-dark-400 hover:text-accent-danger"><Trash2 size={16} /></button>
                                                            </>
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                        ))}
                                        {libraryVariables.length === 0 && !isAddingVar && (
                                            <tr>
                                                <td colSpan={6} className="px-6 py-8 text-center text-dark-500">
                                                    No library variables defined. Add one to create a default variable template.
                                                </td>
                                            </tr>
                                        )}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}
