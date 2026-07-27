import { useState, useEffect } from 'react'
import { Plus, Trash2, Save, X, Edit2, Globe, Shield, ChevronDown, ChevronRight, Server } from 'lucide-react'
import { adDomainsApi } from '../services/api'
import toast from 'react-hot-toast'

const TRUST_TYPES = [
    { value: '', label: 'None' },
    { value: 'parent', label: 'Parent (Forest Root)' },
    { value: 'child', label: 'Child Domain' },
    { value: 'forest_trust', label: 'Forest Trust' },
    { value: 'external', label: 'External Trust' },
    { value: 'shortcut', label: 'Shortcut Trust' },
]

const EMPTY_DOMAIN = {
    name: '',
    netbios_name: '',
    dc_ip: '',
    dc_fqdn: '',
    description: '',
    trust_type: '',
    parent_domain_id: null,
}

export default function ADDomainManager({ projectId, domains, onDomainsChange }) {
    const [isAdding, setIsAdding] = useState(false)
    const [newDomain, setNewDomain] = useState({ ...EMPTY_DOMAIN })
    const [editingId, setEditingId] = useState(null)
    const [editData, setEditData] = useState({})
    const [expandedIds, setExpandedIds] = useState(new Set())

    const toggleExpand = (id) => {
        setExpandedIds(prev => {
            const next = new Set(prev)
            next.has(id) ? next.delete(id) : next.add(id)
            return next
        })
    }

    const handleCreate = async () => {
        if (!newDomain.name) return
        try {
            await adDomainsApi.create({ ...newDomain, project_id: projectId })
            toast.success(`Domain "${newDomain.name}" added`)
            setNewDomain({ ...EMPTY_DOMAIN })
            setIsAdding(false)
            onDomainsChange()
        } catch (error) {
            toast.error('Failed to create domain')
        }
    }

    const handleUpdate = async (id) => {
        try {
            await adDomainsApi.update(id, editData)
            toast.success('Domain updated')
            setEditingId(null)
            onDomainsChange()
        } catch (error) {
            toast.error('Failed to update domain')
        }
    }

    const handleDelete = async (id, name) => {
        if (!confirm(`Delete domain "${name}"? Variables and checklists linked to this domain will be unlinked.`)) return
        try {
            await adDomainsApi.delete(id)
            toast.success('Domain deleted')
            onDomainsChange()
        } catch (error) {
            toast.error('Failed to delete domain')
        }
    }

    const startEdit = (d) => {
        setEditingId(d.id)
        setEditData({
            name: d.name,
            netbios_name: d.netbios_name || '',
            dc_ip: d.dc_ip || '',
            dc_fqdn: d.dc_fqdn || '',
            description: d.description || '',
            trust_type: d.trust_type || '',
            parent_domain_id: d.parent_domain_id,
        })
    }

    const parentDomains = domains.filter(d => !d.parent_domain_id || d.trust_type === 'parent')

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <Shield size={18} className="text-purple-400" />
                    <h3 className="text-lg font-semibold text-dark-50">AD Domains</h3>
                    <span className="text-xs text-dark-500">({domains.length})</span>
                </div>
                <button onClick={() => setIsAdding(true)} className="btn-primary btn-sm">
                    <Plus size={16} /> Add Domain
                </button>
            </div>

            {isAdding && (
                <div className="card border-accent-primary/30 p-4 space-y-3">
                    <h4 className="text-sm font-medium text-dark-200">New AD Domain</h4>
                    <div className="grid grid-cols-2 gap-3">
                        <input
                            autoFocus
                            className="input-field bg-dark-900 border-dark-700 text-sm"
                            placeholder="Domain Name (e.g. corp.local)"
                            value={newDomain.name}
                            onChange={e => setNewDomain({ ...newDomain, name: e.target.value })}
                        />
                        <input
                            className="input-field bg-dark-900 border-dark-700 text-sm"
                            placeholder="NetBIOS Name (e.g. CORP)"
                            value={newDomain.netbios_name}
                            onChange={e => setNewDomain({ ...newDomain, netbios_name: e.target.value })}
                        />
                        <input
                            className="input-field bg-dark-900 border-dark-700 text-sm"
                            placeholder="DC IP Address"
                            value={newDomain.dc_ip}
                            onChange={e => setNewDomain({ ...newDomain, dc_ip: e.target.value })}
                        />
                        <input
                            className="input-field bg-dark-900 border-dark-700 text-sm"
                            placeholder="DC FQDN (e.g. dc01.corp.local)"
                            value={newDomain.dc_fqdn}
                            onChange={e => setNewDomain({ ...newDomain, dc_fqdn: e.target.value })}
                        />
                        <select
                            className="input-field bg-dark-900 border-dark-700 text-sm"
                            value={newDomain.trust_type}
                            onChange={e => setNewDomain({ ...newDomain, trust_type: e.target.value })}
                        >
                            {TRUST_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                        </select>
                        {(newDomain.trust_type === 'child' || newDomain.trust_type === 'forest_trust' || newDomain.trust_type === 'external') && (
                            <select
                                className="input-field bg-dark-900 border-dark-700 text-sm"
                                value={newDomain.parent_domain_id || ''}
                                onChange={e => setNewDomain({ ...newDomain, parent_domain_id: e.target.value ? parseInt(e.target.value) : null })}
                            >
                                <option value="">Parent Domain (optional)</option>
                                {domains.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                            </select>
                        )}
                    </div>
                    <input
                        className="input-field bg-dark-900 border-dark-700 text-sm w-full"
                        placeholder="Description (optional)"
                        value={newDomain.description}
                        onChange={e => setNewDomain({ ...newDomain, description: e.target.value })}
                    />
                    <div className="flex justify-end gap-2">
                        <button onClick={() => { setIsAdding(false); setNewDomain({ ...EMPTY_DOMAIN }) }} className="btn-ghost btn-sm">Cancel</button>
                        <button onClick={handleCreate} disabled={!newDomain.name} className="btn-primary btn-sm disabled:opacity-50"><Save size={14} /> Create</button>
                    </div>
                </div>
            )}

            {domains.length === 0 && !isAdding && (
                <div className="card p-6 text-center text-dark-500">
                    <Globe size={32} className="mx-auto mb-2 opacity-40" />
                    <p>No AD domains configured. Add domains to scope variables and checklists per domain.</p>
                </div>
            )}

            <div className="space-y-2">
                {domains.map(d => {
                    const isExpanded = expandedIds.has(d.id)
                    const isEditing = editingId === d.id
                    const children = domains.filter(c => c.parent_domain_id === d.id)
                    const parentName = d.parent_domain_id ? domains.find(p => p.id === d.parent_domain_id)?.name : null

                    return (
                        <div key={d.id} className="card border-dark-700 overflow-hidden">
                            <div
                                className="flex items-center gap-3 p-3 cursor-pointer hover:bg-dark-700/50 transition-colors"
                                onClick={() => toggleExpand(d.id)}
                            >
                                {isExpanded ? <ChevronDown size={16} className="text-dark-400" /> : <ChevronRight size={16} className="text-dark-400" />}
                                <Server size={16} className="text-purple-400" />
                                <span className="font-semibold text-dark-100">{d.name}</span>
                                {d.netbios_name && <span className="text-xs bg-dark-700 px-1.5 py-0.5 rounded text-dark-400">{d.netbios_name}</span>}
                                {d.trust_type && (
                                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                                        d.trust_type === 'parent' ? 'bg-amber-500/15 text-amber-400' :
                                        d.trust_type === 'child' ? 'bg-blue-500/15 text-blue-400' :
                                        d.trust_type === 'forest_trust' ? 'bg-green-500/15 text-green-400' :
                                        'bg-dark-700 text-dark-400'
                                    }`}>
                                        {d.trust_type.replace('_', ' ')}
                                    </span>
                                )}
                                {parentName && <span className="text-xs text-dark-500">→ {parentName}</span>}
                                {children.length > 0 && <span className="text-xs text-dark-500">({children.length} child{children.length > 1 ? 'ren' : ''})</span>}
                                <div className="ml-auto flex items-center gap-1" onClick={e => e.stopPropagation()}>
                                    <button onClick={() => startEdit(d)} className="p-1 text-dark-400 hover:text-accent-primary"><Edit2 size={14} /></button>
                                    <button onClick={() => handleDelete(d.id, d.name)} className="p-1 text-dark-400 hover:text-accent-danger"><Trash2 size={14} /></button>
                                </div>
                            </div>

                            {isExpanded && !isEditing && (
                                <div className="px-4 pb-3 pt-1 border-t border-dark-700/50 grid grid-cols-2 gap-2 text-sm">
                                    <div><span className="text-dark-500">DC IP:</span> <span className="text-dark-200 font-mono">{d.dc_ip || '—'}</span></div>
                                    <div><span className="text-dark-500">DC FQDN:</span> <span className="text-dark-200 font-mono">{d.dc_fqdn || '—'}</span></div>
                                    {d.description && <div className="col-span-2"><span className="text-dark-500">Description:</span> <span className="text-dark-300">{d.description}</span></div>}
                                </div>
                            )}

                            {isEditing && (
                                <div className="px-4 pb-3 pt-2 border-t border-dark-700/50 space-y-3">
                                    <div className="grid grid-cols-2 gap-3">
                                        <input className="input-field bg-dark-900 border-dark-700 text-sm" placeholder="Domain Name" value={editData.name} onChange={e => setEditData({ ...editData, name: e.target.value })} />
                                        <input className="input-field bg-dark-900 border-dark-700 text-sm" placeholder="NetBIOS" value={editData.netbios_name} onChange={e => setEditData({ ...editData, netbios_name: e.target.value })} />
                                        <input className="input-field bg-dark-900 border-dark-700 text-sm" placeholder="DC IP" value={editData.dc_ip} onChange={e => setEditData({ ...editData, dc_ip: e.target.value })} />
                                        <input className="input-field bg-dark-900 border-dark-700 text-sm" placeholder="DC FQDN" value={editData.dc_fqdn} onChange={e => setEditData({ ...editData, dc_fqdn: e.target.value })} />
                                        <select className="input-field bg-dark-900 border-dark-700 text-sm" value={editData.trust_type} onChange={e => setEditData({ ...editData, trust_type: e.target.value })}>
                                            {TRUST_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                                        </select>
                                        {(editData.trust_type === 'child' || editData.trust_type === 'forest_trust' || editData.trust_type === 'external') && (
                                            <select className="input-field bg-dark-900 border-dark-700 text-sm" value={editData.parent_domain_id || ''} onChange={e => setEditData({ ...editData, parent_domain_id: e.target.value ? parseInt(e.target.value) : null })}>
                                                <option value="">Parent Domain</option>
                                                {domains.filter(p => p.id !== d.id).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                                            </select>
                                        )}
                                    </div>
                                    <input className="input-field bg-dark-900 border-dark-700 text-sm w-full" placeholder="Description" value={editData.description} onChange={e => setEditData({ ...editData, description: e.target.value })} />
                                    <div className="flex justify-end gap-2">
                                        <button onClick={() => setEditingId(null)} className="btn-ghost btn-sm">Cancel</button>
                                        <button onClick={() => handleUpdate(d.id)} className="btn-primary btn-sm"><Save size={14} /> Save</button>
                                    </div>
                                </div>
                            )}
                        </div>
                    )
                })}
            </div>
        </div>
    )
}
