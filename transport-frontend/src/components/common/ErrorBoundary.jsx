/**
 * Error Boundary Component
 * Catches errors in the component tree and displays a fallback UI
 */

import React from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { AlertCircle } from 'lucide-react';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      errorCount: 0
    };
  }

  static getDerivedStateFromError(error) {
    return {
      hasError: true
    };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Error caught by boundary:', error, errorInfo);
    
    // Log to external error reporting service if available
    if (window.errorReportingService) {
      window.errorReportingService.captureException(error, {
        errorInfo,
        componentStack: errorInfo.componentStack
      });
    }

    this.setState(prev => ({
      error,
      errorInfo,
      errorCount: prev.errorCount + 1
    }));
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null
    });
  };

  render() {
    if (this.state.hasError) {
      return (
        <Box sx={{ p: 3, minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Paper elevation={1} sx={{ p: 4, maxWidth: 600, width: '100%' }}>
            <Stack spacing={3} alignItems="center">
              <AlertCircle size={48} color="#d32f2f" />
              
              <Typography variant="h5" fontWeight={700} align="center">
                Oops! Something went wrong
              </Typography>
              
              <Typography variant="body2" color="text.secondary" align="center">
                We encountered an unexpected error. Please try refreshing the page or contact support if the problem persists.
              </Typography>

              <Alert severity="error" sx={{ width: '100%' }}>
                <Typography variant="caption" component="div" sx={{ mb: 1, fontWeight: 600 }}>
                  Error Details:
                </Typography>
                <Typography variant="caption" component="div" sx={{ fontFamily: 'monospace', wordBreak: 'break-word' }}>
                  {this.state.error?.toString()}
                </Typography>
                {process.env.NODE_ENV === 'development' && this.state.errorInfo && (
                  <Typography variant="caption" component="div" sx={{ mt: 1, fontFamily: 'monospace', fontSize: '0.7rem', maxHeight: 200, overflow: 'auto' }}>
                    {this.state.errorInfo.componentStack}
                  </Typography>
                )}
              </Alert>

              <Stack direction="row" spacing={2} sx={{ width: '100%' }}>
                <Button
                  variant="contained"
                  onClick={this.handleReset}
                  sx={{ flex: 1 }}
                >
                  Try Again
                </Button>
                <Button
                  variant="outlined"
                  onClick={() => window.location.href = '/'}
                  sx={{ flex: 1 }}
                >
                  Go Home
                </Button>
              </Stack>

              {this.state.errorCount > 3 && (
                <Alert severity="warning">
                  <Typography variant="caption">
                    Multiple errors detected. Try refreshing the page or clearing your browser cache.
                  </Typography>
                </Alert>
              )}
            </Stack>
          </Paper>
        </Box>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
