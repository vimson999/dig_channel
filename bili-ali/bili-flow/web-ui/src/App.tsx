import React, { useState, useEffect } from 'react';
import { 
  Box, 
  CssBaseline, 
  AppBar, 
  Toolbar, 
  Typography, 
  Container, 
  Paper, 
  Stepper, 
  Step, 
  StepLabel, 
  Button, 
  TextField, 
  List, 
  ListItem, 
  ListItemText, 
  Divider,
  CircularProgress,
  Alert,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  RadioGroup,
  FormControlLabel,
  Radio,
  FormLabel
} from '@mui/material';
import { 
  Settings as SettingsIcon, 
  CloudUpload as UploadIcon, 
  CheckCircle as CheckCircleIcon, 
  Refresh as RefreshIcon, 
  Description as FileIcon,
  Delete as DeleteIcon,
  Add as AddIcon,
  DragHandle as DragHandleIcon,
  Edit as EditIcon,
  PhotoCamera as PhotoCameraIcon,
  Image as ImageIcon,
  VideoFile as VideoFileIcon,
  DeleteSweep as DeleteSweepIcon
} from '@mui/icons-material';
import axios from 'axios';

// --- API ---
const api = axios.create({ baseURL: '/api' });

// --- Components ---

const SetupStep = ({ onNext }: { onNext: () => void }) => {
  const [config, setConfig] = useState({ SESSDATA: '', BILI_JCT: '', BUVID3: '', DEDEUSERID: '' });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get('/config').then(res => setConfig(res.data));
  }, []);

  const handleSave = async () => {
    setLoading(true);
    try {
      await api.post('/config', config);
      onNext();
    } catch (e) {
      alert('Save failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box sx={{ mt: 3 }}>
      <Typography variant="h6" gutterBottom>1. 配置 Bilibili 凭证</Typography>
      <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
        请填入您的浏览器 Cookie 信息。这些信息将加密存储在本地 .env 文件中。
      </Typography>
      <Box component="form" sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <TextField label="SESSDATA" fullWidth value={config.SESSDATA} onChange={e => setConfig({...config, SESSDATA: e.target.value})} />
        <TextField label="BILI_JCT" fullWidth value={config.BILI_JCT} onChange={e => setConfig({...config, BILI_JCT: e.target.value})} />
        <TextField label="BUVID3" fullWidth value={config.BUVID3} onChange={e => setConfig({...config, BUVID3: e.target.value})} />
        <TextField label="DEDEUSERID (UID)" fullWidth value={config.DEDEUSERID} onChange={e => setConfig({...config, DEDEUSERID: e.target.value})} />
        <Button variant="contained" onClick={handleSave} disabled={loading}>
          {loading ? <CircularProgress size={24} /> : '保存并下一步'}
        </Button>
      </Box>
    </Box>
  );
};

const VerifyStep = ({ onNext, onBack }: { onNext: () => void, onBack: () => void }) => {
  const [status, setStatus] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const check = async () => {
    setLoading(true);
    try {
      const res = await api.get('/status/login');
      setStatus(res.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { check(); }, []);

  return (
    <Box sx={{ mt: 3, textAlign: 'center' }}>
      <Typography variant="h6" gutterBottom>2. 验证登录状态</Typography>
      {loading ? <CircularProgress /> : (
        status?.valid ? (
          <Alert severity="success" icon={<CheckCircleIcon />} sx={{ mt: 2, alignItems: 'center' }}>
            登录成功：{status.username} (Lv.{status.level})
          </Alert>
        ) : (
          <Alert severity="error" sx={{ mt: 2 }}>验证失败: {status?.message || '未知错误'}</Alert>
        )
      )}
      <Box sx={{ mt: 4, display: 'flex', gap: 2, justifyContent: 'center' }}>
        <Button onClick={onBack}>返回修改</Button>
        <Button variant="contained" color="primary" disabled={!status?.valid} onClick={onNext}>
          准备上传
        </Button>
        <IconButton onClick={check}><RefreshIcon /></IconButton>
      </Box>
    </Box>
  );
};

const TaskRegistrationDialog = ({ open, onClose, onSuccess, initialFilename }: { open: boolean, onClose: () => void, onSuccess: () => void, initialFilename?: string }) => {
  const [meta, setMeta] = useState({ title: '', desc: '', tid: 171, tags: '', cover_path: '', copyright: 1, dynamic: '' });
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Mode: 'create' or 'edit'
  const isEdit = !!initialFilename;

  useEffect(() => {
    if (open) {
      if (initialFilename) {
        // Edit Mode: Load existing metadata
        setLoading(true);
        api.get(`/files/input/${encodeURIComponent(initialFilename)}/metadata`)
          .then(res => {
            setMeta({ 
              ...res.data, 
              tags: res.data.tags.join(','),
              copyright: res.data.copyright || 1,
              dynamic: res.data.dynamic || ''
            });
          })
          .finally(() => setLoading(false));
      } else {
        // Create Mode: Reset form
        setMeta({ title: '', desc: '', tid: 171, tags: '', cover_path: '', copyright: 1, dynamic: '' });
        setVideoFile(null);
        setCoverFile(null);
      }
    }
  }, [open, initialFilename]);

  const handleVideoSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (event.target.files && event.target.files[0]) {
      const file = event.target.files[0];
      setVideoFile(file);
      // Auto-fill title from filename if empty
      if (!meta.title) {
        setMeta(prev => ({ ...prev, title: file.name.replace(/\.[^/.]+$/, "") }));
      }
    }
  };

  const handleCoverSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    if (event.target.files && event.target.files[0]) {
      setCoverFile(event.target.files[0]);
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      let targetFilename = initialFilename;

      // 1. Upload Video (if create mode)
      if (!isEdit) {
        if (!videoFile) return;
        const formData = new FormData();
        formData.append('file', videoFile);
        await api.post('/files/upload', formData, {
            headers: { 'Content-Type': 'multipart/form-data' }
        });
        targetFilename = videoFile.name;
      }

      if (!targetFilename) return;

      // 2. Upload Cover (if selected)
      let finalCoverPath = meta.cover_path;
      if (coverFile) {
        const formData = new FormData();
        formData.append('file', coverFile);
        const res = await api.post(`/files/input/${encodeURIComponent(targetFilename)}/cover`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' }
        });
        finalCoverPath = res.data.cover_path;
      }

      // 3. Save Metadata
      await api.post(`/files/input/${encodeURIComponent(targetFilename)}/metadata`, {
        ...meta,
        cover_path: finalCoverPath,
        tags: meta.tags.split(',').map(t => t.trim()).filter(Boolean)
      });

      onSuccess();
      onClose();
    } catch (e) {
      alert('操作失败，请重试');
      console.error(e);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>{isEdit ? `编辑任务: ${initialFilename}` : '登记新任务'}</DialogTitle>
      <DialogContent>
        {loading ? <CircularProgress /> : (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3, mt: 1 }}>
            
            {/* Video Selection (Only in Create Mode) */}
            {!isEdit && (
              <Box sx={{ p: 2, border: '1px solid #ccc', borderRadius: 2, display: 'flex', alignItems: 'center', gap: 2 }}>
                <VideoFileIcon fontSize="large" color={videoFile ? "primary" : "disabled"} />
                <Box sx={{ flexGrow: 1 }}>
                    <Typography variant="subtitle1">{videoFile ? videoFile.name : "请选择视频文件"}</Typography>
                    {videoFile && <Typography variant="caption">{(videoFile.size / 1024 / 1024).toFixed(2)} MB</Typography>}
                </Box>
                <Button component="label" variant="outlined" size="small">
                    选择视频
                    <input type="file" hidden accept="video/*" onChange={handleVideoSelect} />
                </Button>
              </Box>
            )}

            {/* Cover Upload Area */}
            <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', p: 2, border: '1px dashed #555', borderRadius: 2 }}>
              {(coverFile || meta.cover_path) ? (
                <Box 
                  component="img" 
                  src={coverFile ? URL.createObjectURL(coverFile) : `/api/files/cover-preview?path=${encodeURIComponent(meta.cover_path)}`}
                  sx={{ width: 160, height: 90, objectFit: 'cover', bgcolor: '#000' }}
                  onError={(e: any) => e.target.src = 'https://via.placeholder.com/160x90?text=Cover'}
                />
              ) : (
                <Box sx={{ width: 160, height: 90, bgcolor: '#eee', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <ImageIcon color="disabled" />
                </Box>
              )}
              <Box>
                <Typography variant="subtitle2" gutterBottom>视频封面 (可选)</Typography>
                <Button 
                  component="label" 
                  variant="outlined" 
                  startIcon={<PhotoCameraIcon />} 
                  size="small"
                >
                  选择图片
                  <input type="file" hidden accept="image/*" onChange={handleCoverSelect} />
                </Button>
                <Typography variant="caption" display="block" color="textSecondary" sx={{ mt: 1 }}>
                  支持 JPG, PNG. 若不上传则尝试自动截图
                </Typography>
              </Box>
            </Box>

            {/* Metadata Fields */}
            <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2 }}>
              <TextField 
                label="视频标题" 
                fullWidth 
                required 
                value={meta.title} 
                onChange={e => setMeta({...meta, title: e.target.value})} 
              />
              <FormControl>
                <FormLabel>类型</FormLabel>
                <RadioGroup row value={meta.copyright} onChange={e => setMeta({...meta, copyright: Number(e.target.value)})}>
                  <FormControlLabel value={1} control={<Radio />} label="自制" />
                  <FormControlLabel value={2} control={<Radio />} label="转载" />
                </RadioGroup>
              </FormControl>
            </Box>

            <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2 }}>
               <FormControl fullWidth>
                <InputLabel>分区 (TID)</InputLabel>
                <Select value={meta.tid} label="分区 (TID)" onChange={(e) => setMeta({...meta, tid: Number(e.target.value)})}>
                  <MenuItem value={171}>电子竞技</MenuItem>
                  <MenuItem value={17}>单机游戏</MenuItem>
                  <MenuItem value={65}>网络游戏</MenuItem>
                  <MenuItem value={122}>野生技术协会 (科技)</MenuItem>
                  <MenuItem value={21}>日常 (生活)</MenuItem>
                  <MenuItem value={138}>搞笑</MenuItem>
                  <MenuItem value={36}>知识</MenuItem>
                  <MenuItem value={188}>数码</MenuItem>
                  <MenuItem value={157}>美食</MenuItem>
                  <MenuItem value={158}>动物圈</MenuItem>
                  <MenuItem value={154}>运动</MenuItem>
                  <MenuItem value={160}>生活</MenuItem>
                </Select>
              </FormControl>
              <TextField label="标签 (逗号分隔)" fullWidth value={meta.tags} onChange={e => setMeta({...meta, tags: e.target.value})} />
            </Box>

            <TextField label="简介" fullWidth multiline rows={3} value={meta.desc} onChange={e => setMeta({...meta, desc: e.target.value})} />
            
            <TextField 
              label="动态文案 (可选)" 
              fullWidth 
              value={meta.dynamic} 
              onChange={e => setMeta({...meta, dynamic: e.target.value})} 
            />
            
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={submitting}>取消</Button>
        <Button 
            onClick={handleSubmit} 
            variant="contained" 
            disabled={submitting || (!isEdit && !videoFile) || !meta.title}
        >
          {submitting ? '处理中...' : (isEdit ? '保存修改' : '登记任务')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

const UploadStep = () => {
  const [files, setFiles] = useState<any[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [processing, setProcessing] = useState(false);
  const [editingFile, setEditingFile] = useState<string | null>(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  const fetchFiles = () => api.get('/files/input').then(res => setFiles(res.data));
  
  // Drag & Drop State
  const [draggedItem, setDraggedItem] = useState<any>(null);

  const handleDragStart = (e: React.DragEvent, item: any) => {
    setDraggedItem(item);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    const draggedOverItem = files[index];
    if (draggedItem === draggedOverItem) return;
    let items = files.filter(item => item !== draggedItem);
    items.splice(index, 0, draggedItem);
    setFiles(items);
  };

  const handleDragEnd = async () => {
    setDraggedItem(null);
    const order = files.map(f => f.name);
    try {
      await api.post('/files/reorder', order);
    } catch (e) {
      console.error("Failed to save order", e);
    }
  };

  const handleDelete = async (filename: string) => {
    if (!window.confirm(`确定要删除 ${filename} 吗？`)) return;
    try {
      await api.delete(`/files/input/${encodeURIComponent(filename)}`);
      fetchFiles();
    } catch (e) {
      alert('删除失败');
    }
  };

  const handleClearAll = async () => {
    if (!window.confirm('确定要清空待上传队列吗？所有文件将被删除！')) return;
    try {
      await api.delete('/files/input');
      fetchFiles();
    } catch (e) {
      alert('清空失败');
    }
  };

  useEffect(() => {
    fetchFiles();
    const eventSource = new EventSource('/api/logs/stream');
    eventSource.onmessage = (event) => {
      setLogs(prev => [...prev.slice(-99), event.data]);
    };
    return () => eventSource.close();
  }, []);

  const startUpload = async () => {
    setProcessing(true);
    await api.post('/tasks/upload');
  };

  return (
    <Box sx={{ mt: 3 }}>
      <Typography variant="h6" gutterBottom>3. 任务工作台</Typography>
      
      <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
        <Button
          variant="outlined"
          startIcon={<AddIcon />}
          disabled={processing}
          onClick={() => setCreateDialogOpen(true)}
        >
          登记新视频
        </Button>
        <Button
          variant="text"
          color="error"
          startIcon={<DeleteSweepIcon />}
          disabled={processing || files.length === 0}
          onClick={handleClearAll}
        >
          清空队列
        </Button>
      </Box>

      <Paper variant="outlined" sx={{ p: 2, mb: 2, maxHeight: 200, overflow: 'auto' }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>待上传队列 ({files.length})</Typography>
        <List dense>
          {files.map((f, index) => (
            <ListItem 
              key={f.name}
              draggable
              onDragStart={(e) => handleDragStart(e, f)}
              onDragOver={(e) => handleDragOver(e, index)}
              onDragEnd={handleDragEnd}
              sx={{ 
                cursor: 'move',
                bgcolor: draggedItem === f ? 'action.hover' : 'transparent',
                transition: 'all 0.2s'
              }}
              secondaryAction={
                <Box>
                  <IconButton onClick={() => setEditingFile(f.name)}>
                    <EditIcon color="primary" />
                  </IconButton>
                  <IconButton edge="end" aria-label="delete" onClick={() => handleDelete(f.name)}>
                    <DeleteIcon color="error" />
                  </IconButton>
                </Box>
              }
            >
              <DragHandleIcon sx={{ mr: 2, color: 'text.disabled' }} />
              <FileIcon sx={{ mr: 1, color: 'action.active' }} />
              <ListItemText primary={f.name} secondary={`${(f.size/1024/1024).toFixed(2)} MB`} />
            </ListItem>
          ))}
          {files.length === 0 && <Typography color="textSecondary">队列为空，请点击“登记新视频”添加任务</Typography>}
        </List>
      </Paper>
      
      <TaskRegistrationDialog 
        open={!!editingFile || createDialogOpen} 
        initialFilename={editingFile || undefined}
        onClose={() => { setEditingFile(null); setCreateDialogOpen(false); }}
        onSuccess={fetchFiles}
      />

      <Button 
        variant="contained" 
        fullWidth 
        size="large" 
        startIcon={<UploadIcon />} 
        disabled={processing || files.length === 0}
        onClick={startUpload}
      >
        {processing ? '正在处理中...' : '开始批量上传'}
      </Button>

      <Typography variant="subtitle2" sx={{ mt: 3, mb: 1 }}>实时日志</Typography>
      <Paper sx={{ p: 2, bgcolor: '#1e1e1e', color: '#d4d4d4', fontFamily: 'monospace', height: 300, overflow: 'auto' }}>
        {logs.map((log, i) => <div key={i} style={{ fontSize: '0.85rem', marginBottom: '4px' }}>{log}</div>)}
        <div id="logs-end" />
      </Paper>
      <Button size="small" onClick={fetchFiles} sx={{ mt: 1 }}>刷新文件列表</Button>
    </Box>
  );
};

// --- Main App ---

export default function App() {
  const [activeStep, setActiveStep] = useState(0);
  const steps = ['环境配置', '状态验证', '执行任务'];

  return (
    <Box sx={{ display: 'flex' }}>
      <CssBaseline />
      <AppBar position="fixed">
        <Toolbar>
          <Typography variant="h6" noWrap component="div">
            Bili-Flow 首席研发控制台
          </Typography>
        </Toolbar>
      </AppBar>
      <Container maxWidth="md" sx={{ mt: 12, mb: 4 }}>
        <Paper elevation={3} sx={{ p: 4, borderRadius: 2 }}>
          <Stepper activeStep={activeStep} alternativeLabel>
            {steps.map((label) => (
              <Step key={label}>
                <StepLabel>{label}</StepLabel>
              </Step>
            ))}
          </Stepper>

          {activeStep === 0 && <SetupStep onNext={() => setActiveStep(1)} />}
          {activeStep === 1 && <VerifyStep onNext={() => setActiveStep(2)} onBack={() => setActiveStep(0)} />}
          {activeStep === 2 && <UploadStep />}
        </Paper>
      </Container>
    </Box>
  );
}
