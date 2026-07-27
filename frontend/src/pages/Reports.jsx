import { useState, useEffect } from 'react'
import { FileText, Download, Trash2, Loader2, Plus } from 'lucide-react'
import { reportsApi } from '../services/api'
import { useProjectsStore } from '../stores'
import toast from 'react-hot-toast'

export default function Reports() {
    const { projects, fetchProjects } = useProjectsStore()
    const [reports, setReports] = useState([])
    const [loading, setLoading] = useState(true)
    const [generating, setGenerating] = useState(false)
    const [showGenerateModal, setShowGenerateModal] = useState(false)
    const [generateOptions, setGenerateOptions] = useState({
        projectId: '',
        format: 'markdown',
        includeLogs: true,
        includeFiles: true
    })

    useEffect(() => {
        loadReports()
        fetchProjects()
    }, [fetchProjects])

    const loadReports = async () => {
        try {
            const { data } = await reportsApi.list()
            setReports(data.reports)
        } catch (error) {
            console.error('Failed to load reports')
        } finally {
            setLoading(false)
        }
    }

    const handleGenerate = async () => {
        if (!generateOptions.projectId) {
            toast.error('Please select a project')
            return
        }

        setGenerating(true)
        try {
            const response = await reportsApi.generate(generateOptions.projectId, {
                format: generateOptions.format,
                include_logs: generateOptions.includeLogs,
                include_files: generateOptions.includeFiles
            })

            // Download the file
            const url = window.URL.createObjectURL(new Blob([response.data]))
            const link = document.createElement('a')
            link.href = url
            link.setAttribute('download', response.headers['content-disposition']?.split('filename=')[1] || 'report.md')
            document.body.appendChild(link)
            link.click()
            link.remove()

            toast.success('Report generated successfully')
            setShowGenerateModal(false)
            loadReports()
        } catch (error) {
            toast.error('Failed to generate report')
        } finally {
            setGenerating(false)
        }
    }

    const handleDownload = async (filename) => {
        try {
            const response = await reportsApi.download(filename)
            const url = window.URL.createObjectURL(new Blob([response.data]))
            const link = document.createElement('a')
            link.href = url
            link.setAttribute('download', filename)
            document.body.appendChild(link)
            link.click()
            link.remove()
        } catch (error) {
            toast.error('Failed to download report')
        }
    }

    return (
        <div className="max-w-4xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-dark-50">Reports</h1>
                    <p className="text-dark-400 mt-1">Generate and manage penetration test reports</p>
                </div>
                <button onClick={() => setShowGenerateModal(true)} className="btn-primary">
                    <Plus size={18} />
                    Generate Report
                </button>
            </div>

            {/* Reports List */}
            <div className="card">
                <h3 className="font-semibold text-dark-100 mb-4">Generated Reports</h3>

                {loading ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 size={24} className="animate-spin text-accent-primary" />
                    </div>
                ) : reports.length === 0 ? (
                    <div className="text-center py-8">
                        <FileText size={48} className="mx-auto text-dark-600 mb-4" />
                        <p className="text-dark-400">No reports generated yet</p>
                        <p className="text-dark-500 text-sm">Generate your first report from a project</p>
                    </div>
                ) : (
                    <div className="space-y-2">
                        {reports.map((report, idx) => (
                            <div
                                key={idx}
                                className="flex items-center justify-between p-4 bg-dark-800/50 rounded-lg hover:bg-dark-800 transition-colors"
                            >
                                <div className="flex items-center gap-4">
                                    <div className={`p-2 rounded-lg ${report.name.endsWith('.pdf') ? 'bg-accent-danger/10' : 'bg-accent-info/10'
                                        }`}>
                                        <FileText size={20} className={
                                            report.name.endsWith('.pdf') ? 'text-accent-danger' : 'text-accent-info'
                                        } />
                                    </div>
                                    <div>
                                        <h4 className="text-dark-100 font-medium">{report.name}</h4>
                                        <div className="flex items-center gap-4 text-sm text-dark-500">
                                            <span>{formatSize(report.size)}</span>
                                            <span>{new Date(report.created).toLocaleString()}</span>
                                        </div>
                                    </div>
                                </div>
                                <button
                                    onClick={() => handleDownload(report.name)}
                                    className="btn-secondary btn-sm"
                                >
                                    <Download size={16} />
                                    Download
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Generate Modal */}
            {showGenerateModal && (
                <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
                    <div className="card max-w-md w-full animate-slide-in">
                        <h2 className="text-xl font-bold text-dark-50 mb-6">Generate Report</h2>

                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-dark-300 mb-1">Project</label>
                                <select
                                    value={generateOptions.projectId}
                                    onChange={(e) => setGenerateOptions({ ...generateOptions, projectId: e.target.value })}
                                    className="input"
                                >
                                    <option value="">Select a project</option>
                                    {projects.map((p) => (
                                        <option key={p.id} value={p.id}>{p.name}</option>
                                    ))}
                                </select>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-dark-300 mb-1">Format</label>
                                <div className="flex gap-4">
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="radio"
                                            name="format"
                                            value="markdown"
                                            checked={generateOptions.format === 'markdown'}
                                            onChange={(e) => setGenerateOptions({ ...generateOptions, format: e.target.value })}
                                            className="text-accent-primary focus:ring-accent-primary"
                                        />
                                        <span className="text-dark-300">Markdown</span>
                                    </label>
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="radio"
                                            name="format"
                                            value="html"
                                            checked={generateOptions.format === 'html'}
                                            onChange={(e) => setGenerateOptions({ ...generateOptions, format: e.target.value })}
                                            className="text-accent-primary focus:ring-accent-primary"
                                        />
                                        <span className="text-dark-300">HTML</span>
                                    </label>
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="radio"
                                            name="format"
                                            value="pdf"
                                            checked={generateOptions.format === 'pdf'}
                                            onChange={(e) => setGenerateOptions({ ...generateOptions, format: e.target.value })}
                                            className="text-accent-primary focus:ring-accent-primary"
                                        />
                                        <span className="text-dark-300">PDF</span>
                                    </label>
                                </div>
                            </div>

                            <div className="space-y-2">
                                <label className="block text-sm font-medium text-dark-300">Include</label>
                                <label className="flex items-center gap-2 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={generateOptions.includeLogs}
                                        onChange={(e) => setGenerateOptions({ ...generateOptions, includeLogs: e.target.checked })}
                                        className="rounded border-dark-600 bg-dark-800 text-accent-primary focus:ring-accent-primary"
                                    />
                                    <span className="text-dark-300">Execution logs</span>
                                </label>
                                <label className="flex items-center gap-2 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={generateOptions.includeFiles}
                                        onChange={(e) => setGenerateOptions({ ...generateOptions, includeFiles: e.target.checked })}
                                        className="rounded border-dark-600 bg-dark-800 text-accent-primary focus:ring-accent-primary"
                                    />
                                    <span className="text-dark-300">Discovered files</span>
                                </label>
                            </div>

                            <div className="flex gap-3 pt-4">
                                <button
                                    onClick={() => setShowGenerateModal(false)}
                                    className="btn-secondary flex-1"
                                    disabled={generating}
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={handleGenerate}
                                    className="btn-primary flex-1"
                                    disabled={generating}
                                >
                                    {generating ? (
                                        <>
                                            <Loader2 size={18} className="animate-spin" />
                                            Generating...
                                        </>
                                    ) : (
                                        <>
                                            <FileText size={18} />
                                            Generate
                                        </>
                                    )}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

function formatSize(bytes) {
    if (!bytes) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB']
    let size = bytes
    let unitIndex = 0
    while (size >= 1024 && unitIndex < units.length - 1) {
        size /= 1024
        unitIndex++
    }
    return `${size.toFixed(1)} ${units[unitIndex]}`
}
