import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
    Plus, FolderOpen, Calendar, Target, ChevronRight,
    CheckCircle2, Clock, AlertCircle, MoreVertical, Trash2, RotateCw, X, Globe
} from 'lucide-react'
import { useProjectsStore } from '../stores'
import toast from 'react-hot-toast'

export default function Dashboard() {
    const { projects, loading, fetchProjects, createProject, deleteProject, migrateOrphans } = useProjectsStore()
    const [showCreateModal, setShowCreateModal] = useState(false)
    const [newProject, setNewProject] = useState({ name: '', description: '', scope: { ips: [], cidrs: [], fqdns: [] }, domains: [] })
    const [domainInput, setDomainInput] = useState('')
    const [migrating, setMigrating] = useState(false)

    const addDomain = () => {
        const d = domainInput.trim()
        if (d && !newProject.domains.includes(d)) {
            setNewProject({ ...newProject, domains: [...newProject.domains, d] })
        }
        setDomainInput('')
    }

    const removeDomain = (domain) => {
        setNewProject({ ...newProject, domains: newProject.domains.filter(d => d !== domain) })
    }

    const handleDomainKeyDown = (e) => {
        if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            addDomain()
        }
    }

    useEffect(() => {
        fetchProjects()
    }, [fetchProjects])

    const handleCreateProject = async (e) => {
        e.preventDefault()
        try {
            await createProject(newProject)
            setShowCreateModal(false)
            setNewProject({ name: '', description: '', scope: { ips: [], cidrs: [], fqdns: [] }, domains: [] })
            setDomainInput('')
            toast.success('Project created successfully')
        } catch (error) {
            toast.error('Failed to create project')
        }
    }

    const handleDeleteProject = async (id, name) => {
        if (confirm(`Delete project "${name}"? This cannot be undone.`)) {
            try {
                await deleteProject(id)
                toast.success('Project deleted')
            } catch (error) {
                toast.error('Failed to delete project')
            }
        }
    }

    const handleMigrateOrphans = async () => {
        setMigrating(true)
        try {
            const result = await migrateOrphans()
            if (result.migrated_count > 0) {
                toast.success(`Migrated ${result.migrated_count} project(s)`)
            } else {
                toast.success('No orphan projects found')
            }
        } catch (error) {
            toast.error('Failed to migrate projects')
        } finally {
            setMigrating(false)
        }
    }

    const getStatusColor = (status) => {
        switch (status) {
            case 'active': return 'text-accent-success bg-accent-success/10'
            case 'completed': return 'text-accent-info bg-accent-info/10'
            case 'paused': return 'text-accent-warning bg-accent-warning/10'
            default: return 'text-dark-400 bg-dark-700'
        }
    }

    const statColorClasses = {
        'accent-primary': { bg: 'bg-accent-primary/10', text: 'text-accent-primary' },
        'accent-success': { bg: 'bg-accent-success/10', text: 'text-accent-success' },
        'accent-info': { bg: 'bg-accent-info/10', text: 'text-accent-info' },
        'accent-warning': { bg: 'bg-accent-warning/10', text: 'text-accent-warning' },
    }

    return (
        <div className="max-w-7xl mx-auto space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-dark-50">Projects</h1>
                    <p className="text-dark-400 mt-1">Manage your penetration testing engagements</p>
                </div>
                <div className="flex items-center gap-3">
                    <button
                        onClick={handleMigrateOrphans}
                        disabled={migrating}
                        className="btn-secondary"
                        title="Migrate projects created before collaboration was enabled"
                    >
                        <RotateCw size={18} className={migrating ? 'animate-spin' : ''} />
                        {migrating ? 'Migrating...' : 'Migrate Old Projects'}
                    </button>
                    <button
                        onClick={() => setShowCreateModal(true)}
                        className="btn-primary"
                    >
                        <Plus size={18} />
                        New Project
                    </button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                {[
                    { label: 'Total Projects', value: projects.length, icon: FolderOpen, colorKey: 'accent-primary' },
                    { label: 'Active', value: projects.filter(p => p.status === 'active').length, icon: Clock, colorKey: 'accent-success' },
                    { label: 'Completed', value: projects.filter(p => p.status === 'completed').length, icon: CheckCircle2, colorKey: 'accent-info' },
                    { label: 'Pending', value: projects.filter(p => p.status === 'planning').length, icon: AlertCircle, colorKey: 'accent-warning' },
                ].map((stat, i) => {
                    const classes = statColorClasses[stat.colorKey] || statColorClasses['accent-primary']
                    return (
                        <div key={i} className="card">
                            <div className="flex items-center justify-between">
                                <div>
                                    <p className="text-dark-400 text-sm">{stat.label}</p>
                                    <p className="text-2xl font-bold text-dark-50 mt-1">{stat.value}</p>
                                </div>
                                <div className={`p-3 rounded-xl ${classes.bg}`}>
                                    <stat.icon className={classes.text} size={24} />
                                </div>
                            </div>
                        </div>
                    )
                })}
            </div>

            {/* Projects Grid */}
            {loading ? (
                <div className="flex items-center justify-center py-12">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-accent-primary"></div>
                </div>
            ) : projects.length === 0 ? (
                <div className="card text-center py-12">
                    <FolderOpen size={48} className="mx-auto text-dark-600 mb-4" />
                    <h3 className="text-lg font-medium text-dark-300 mb-2">No projects yet</h3>
                    <p className="text-dark-500 mb-4">Create your first penetration testing project</p>
                    <button onClick={() => setShowCreateModal(true)} className="btn-primary">
                        <Plus size={18} />
                        Create Project
                    </button>
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {projects.map((project) => (
                        <Link
                            key={project.id}
                            to={`/project/${project.id}`}
                            className="card group hover:border-accent-primary/50 transition-all duration-300"
                        >
                            <div className="flex items-start justify-between mb-4">
                                <div className="flex items-center gap-3">
                                    <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-accent-primary/20 to-accent-secondary/20 
                                flex items-center justify-center">
                                        <Target size={20} className="text-accent-primary" />
                                    </div>
                                    <div>
                                        <h3 className="font-semibold text-dark-100 group-hover:text-accent-primary transition-colors">
                                            {project.name}
                                        </h3>
                                        <span className={`text-xs px-2 py-0.5 rounded-full ${getStatusColor(project.status)}`}>
                                            {project.status}
                                        </span>
                                    </div>
                                </div>
                                <button
                                    onClick={(e) => { e.preventDefault(); handleDeleteProject(project.id, project.name) }}
                                    className="p-1.5 text-dark-500 hover:text-accent-danger hover:bg-dark-800 rounded opacity-0 group-hover:opacity-100 transition-all"
                                >
                                    <Trash2 size={16} />
                                </button>
                            </div>

                            {project.description && (
                                <p className="text-dark-400 text-sm mb-4 line-clamp-2">{project.description}</p>
                            )}

                            <div className="space-y-3">
                                {/* Progress bar */}
                                <div>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span className="text-dark-500">Progress</span>
                                        <span className="text-dark-400">{Math.round(project.checklist_progress || 0)}%</span>
                                    </div>
                                    <div className="progress-bar">
                                        <div className="progress-fill" style={{ width: `${project.checklist_progress || 0}%` }}></div>
                                    </div>
                                </div>

                                <div className="flex items-center justify-between text-sm">
                                    <div className="flex items-center gap-4">
                                        <span className="text-dark-500">
                                            <Target size={14} className="inline mr-1" />
                                            {project.host_count} hosts
                                        </span>
                                    </div>
                                    <ChevronRight size={16} className="text-dark-600 group-hover:text-accent-primary transition-colors" />
                                </div>
                            </div>
                        </Link>
                    ))}
                </div>
            )}

            {/* Create Modal */}
            {showCreateModal && (
                <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
                    <div className="card max-w-md w-full animate-slide-in">
                        <h2 className="text-xl font-bold text-dark-50 mb-6">Create New Project</h2>
                        <form onSubmit={handleCreateProject} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-dark-300 mb-1">Project Name</label>
                                <input
                                    type="text"
                                    value={newProject.name}
                                    onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
                                    className="input"
                                    placeholder="e.g., Client ABC Pentest"
                                    required
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-dark-300 mb-1">Description</label>
                                <textarea
                                    value={newProject.description}
                                    onChange={(e) => setNewProject({ ...newProject, description: e.target.value })}
                                    className="input min-h-[100px]"
                                    placeholder="Brief description of the engagement..."
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-dark-300 mb-1">
                                    <Globe size={14} className="inline mr-1" />
                                    AD Domains
                                </label>
                                <div className="flex gap-2">
                                    <input
                                        type="text"
                                        value={domainInput}
                                        onChange={(e) => setDomainInput(e.target.value)}
                                        onKeyDown={handleDomainKeyDown}
                                        className="input flex-1"
                                        placeholder="e.g., corp.local"
                                    />
                                    <button type="button" onClick={addDomain} disabled={!domainInput.trim()} className="btn-secondary px-3 disabled:opacity-40">
                                        <Plus size={16} />
                                    </button>
                                </div>
                                {newProject.domains.length > 0 && (
                                    <div className="flex flex-wrap gap-2 mt-2">
                                        {newProject.domains.map(d => (
                                            <span key={d} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-purple-500/15 text-purple-300 text-sm">
                                                {d}
                                                <button type="button" onClick={() => removeDomain(d)} className="hover:text-accent-danger">
                                                    <X size={14} />
                                                </button>
                                            </span>
                                        ))}
                                    </div>
                                )}
                                <p className="text-xs text-dark-500 mt-1">Default library templates will be auto-imported for each domain</p>
                            </div>
                            <div className="flex gap-3 pt-4">
                                <button type="button" onClick={() => setShowCreateModal(false)} className="btn-secondary flex-1">
                                    Cancel
                                </button>
                                <button type="submit" className="btn-primary flex-1">
                                    Create Project
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
