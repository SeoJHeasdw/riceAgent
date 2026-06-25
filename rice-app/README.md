# RICE Frontend Application

The desktop application for **RICE** (Runtime-Integrated Conversational Environment) - an AI-powered conversational interface with integrated learning loop capabilities.

## 📋 Table of Contents

- [Overview](#overview)
- [Technology Stack](#technology-stack)
- [Features](#features)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Development](#development)
- [Building & Deployment](#building--deployment)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Troubleshooting](#troubleshooting)

## 🎯 Overview

RICE is a sophisticated desktop application that combines conversational AI with a learning loop system. It provides:

- **Intelligent Chat Interface**: Stream-based conversations with AI models
- **Learning Loop Visualization**: Real-time display of skill creation and user model updates
- **Tool & Plugin Marketplace**: Discover and install MCP tools and plugins
- **Session Management**: Organize conversations with persistent history
- **Model Configuration**: Switch between SaaS and local models with custom settings

## 🛠 Technology Stack

### Core Framework
- **Electron 31.2+**: Cross-platform desktop application framework
- **React 18.3+**: UI library with hooks and functional components
- **TypeScript 5.5+**: Type-safe development
- **Vite 5.3+**: Fast build tool and dev server

### UI & Styling
- **IBM Carbon Design System v11**: Enterprise-grade design system
- **TailwindCSS 3.4+**: Utility-first CSS framework
- **IBM Plex Fonts**: Sans, Mono, and Serif typefaces

### State Management & Data
- **React Hooks**: Custom hooks for chat, learning loop, and marketplace
- **Server-Sent Events (SSE)**: Real-time streaming from runtime
- **REST API**: Communication with RICE runtime backend

### Build & Development
- **electron-builder**: Package and distribute desktop apps
- **ESLint**: Code linting and quality
- **PostCSS**: CSS processing with Autoprefixer

## ✨ Features

### Chat Interface
- Real-time message streaming with SSE
- Multi-session support with history
- Message retry and abort capabilities
- Skill usage indicators
- Tool call visualization

### Learning Loop
- **Skills Panel**: View and manage learned skills
- **User Model Display**: Track confidence scores for user preferences
- **Session Deltas**: See what changed during conversations
- **Skill Creation Notifications**: Toast alerts for new skills

### Marketplace
- **Tool Market**: Browse and install MCP tools
- **Plugin Market**: Discover domain-specific plugins
- **Filtering & Search**: Category-based filtering and tag search
- **Sponsorship System**: Support tool developers with points
- **Installation Progress**: Visual feedback during installation

### Settings
- **Model Selection**: Choose between SaaS and local models
- **Model Configuration**: Adjust temperature, max tokens, context length
- **User Modeling**: Toggle learning loop features
- **API Keys**: Manage authentication credentials

## 🚀 Getting Started

### Prerequisites

- **Node.js 18+** and npm
- **Git** for version control
- Platform-specific build tools (for packaging):
  - macOS: Xcode Command Line Tools
  - Windows: Visual Studio Build Tools
  - Linux: Standard build tools

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd rice/packages/rice-app

# Install dependencies
npm install
```

### Development Mode

```bash
# Start Vite dev server and Electron
npm run electron:dev

# Or run separately:
npm run dev              # Start Vite dev server (http://localhost:5173)
npm run build:electron   # Build Electron files
```

The application will open automatically with hot-reload enabled.

### Building for Production

```bash
# Build for all platforms
npm run build

# Build for specific platform
npm run package:mac      # macOS (DMG + ZIP)
npm run package:win      # Windows (NSIS + ZIP)
```

Built applications will be in `release/<version>/` directory.

## 📁 Project Structure

```
packages/rice-app/
├── electron/                    # Electron main process
│   ├── main.ts                 # Main process entry point
│   ├── preload.ts              # Preload script (IPC bridge)
│   └── runtime.ts              # Runtime manager (lifecycle)
├── src/
│   ├── components/             # React components
│   │   ├── ui/                # Base UI components
│   │   │   ├── Avatar.tsx
│   │   │   ├── Badge.tsx
│   │   │   ├── Button.tsx
│   │   │   ├── Card.tsx
│   │   │   ├── Input.tsx
│   │   │   ├── Modal.tsx
│   │   │   ├── Tabs.tsx
│   │   │   ├── Tag.tsx
│   │   │   └── Toast.tsx
│   │   ├── carbon/            # Carbon-specific components
│   │   │   ├── ChatInput.tsx
│   │   │   ├── MessageList.tsx
│   │   │   ├── Message.tsx
│   │   │   ├── SessionSidebar.tsx
│   │   │   ├── LearningPanel.tsx
│   │   │   ├── SkillsList.tsx
│   │   │   ├── SkillCard.tsx
│   │   │   ├── UserModelDisplay.tsx
│   │   │   ├── SessionDelta.tsx
│   │   │   ├── ModelSettingsPanel.tsx
│   │   │   ├── ToolCard.tsx
│   │   │   ├── PluginCard.tsx
│   │   │   ├── MarketplaceHeader.tsx
│   │   │   ├── InstallationProgress.tsx
│   │   │   ├── SponsorModal.tsx
│   │   │   ├── ArtifactDetailModal.tsx
│   │   │   ├── SkillCreationToast.tsx
│   │   │   ├── ConfidenceIndicator.tsx
│   │   │   └── IconRail.tsx
│   │   ├── Layout.tsx         # Main layout wrapper
│   │   ├── TwoColumnLayout.tsx
│   │   └── ThreeColumnLayout.tsx
│   ├── screens/               # Main application screens
│   │   ├── ChatScreen.tsx
│   │   ├── ToolMarketScreen.tsx
│   │   ├── PluginMarketScreen.tsx
│   │   └── SettingsScreen.tsx
│   ├── hooks/                 # Custom React hooks
│   │   ├── useChat.ts
│   │   ├── useLearningLoop.ts
│   │   └── useMarketplace.ts
│   ├── lib/                   # Utilities and helpers
│   │   ├── api.ts            # Runtime API client
│   │   ├── sse.ts            # SSE streaming client
│   │   ├── types.ts          # TypeScript definitions
│   │   └── mockData.ts       # Development mock data
│   ├── styles/
│   │   └── globals.css       # Global styles + Tailwind
│   ├── App.tsx               # Root component
│   └── main.tsx              # React entry point
├── public/                    # Static assets
├── docs/                      # Documentation
│   ├── ARCHITECTURE.md
│   ├── COMPONENTS.md
│   ├── DESIGN_SYSTEM.md
│   ├── DEVELOPMENT.md
│   ├── API_INTEGRATION.md
│   └── DEPLOYMENT.md
├── index.html                 # HTML entry point
├── package.json
├── vite.config.ts            # Vite configuration
├── tailwind.config.js        # Tailwind configuration
├── postcss.config.js         # PostCSS configuration
├── tsconfig.json             # TypeScript config (React)
├── tsconfig.node.json        # TypeScript config (Node)
├── electron-builder.yml      # Electron builder config
└── README.md                 # This file
```

## 💻 Development

### Available Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Start Vite dev server |
| `npm run build` | Build for production (all platforms) |
| `npm run build:electron` | Build Electron files only |
| `npm run build:react` | Build React app only |
| `npm run electron:dev` | Run Electron in development mode |
| `npm run electron:build` | Build Electron app |
| `npm run package` | Package app for distribution |
| `npm run package:mac` | Package for macOS |
| `npm run package:win` | Package for Windows |
| `npm run preview` | Preview production build |
| `npm run lint` | Run ESLint |
| `npm run type-check` | Run TypeScript type checking |

### Development Workflow

1. **Start Development Server**
   ```bash
   npm run electron:dev
   ```

2. **Make Changes**
   - Edit files in `src/` for React components
   - Edit files in `electron/` for Electron main process
   - Hot reload is enabled for React
   - Electron requires restart for main process changes

3. **Test Changes**
   - Use mock data for development (see `src/lib/mockData.ts`)
   - Check console for errors and warnings
   - Use React DevTools for component inspection

4. **Type Check**
   ```bash
   npm run type-check
   ```

5. **Lint Code**
   ```bash
   npm run lint
   ```

### Design System Guidelines

This project follows IBM Carbon Design System principles:

- **0px border radius**: Flat, sharp corners (no rounded edges)
- **8px spacing scale**: 2/4/8/12/16/24/32/40/48/64/80/96/160
- **IBM Plex fonts**: Sans, Mono, Serif
- **Sentence case**: All UI text uses sentence case (not title case)
- **Dark theme**: g100 theme by default
- **Color tokens**: Use Carbon color tokens (e.g., `text-primary`, `layer-01`)
- **Typography scale**: Use Carbon type tokens (e.g., `body-short-02`, `heading-03`)

See [DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) for detailed guidelines.

### Code Organization

- **Components**: Reusable UI components in `src/components/`
- **Screens**: Top-level views in `src/screens/`
- **Hooks**: Custom React hooks in `src/hooks/`
- **Utils**: Helper functions in `src/lib/`
- **Types**: TypeScript definitions in `src/lib/types.ts`

### Mock Data

For development without a running backend, use mock data:

```typescript
import { mockMessages, mockSessions, mockTools } from '@lib/mockData';
```

Mock data includes:
- Sample messages and sessions
- Tool and plugin marketplace data
- User model signals and skills
- Model configurations

## 🏗 Building & Deployment

### Production Build

```bash
# Build React app and Electron
npm run build
```

This creates:
- `dist/`: React production build
- `dist-electron/`: Compiled Electron files
- `release/<version>/`: Packaged applications

### Platform-Specific Builds

```bash
# macOS
npm run package:mac
# Creates: RICE-<version>.dmg and RICE-<version>-mac.zip

# Windows
npm run package:win
# Creates: RICE Setup <version>.exe and RICE-<version>-win.zip

# Linux (requires Linux host)
npm run package
# Creates: RICE-<version>.AppImage and RICE-<version>.deb
```

### Code Signing

For distribution, configure code signing:

**macOS:**
```bash
export CSC_LINK=/path/to/certificate.p12
export CSC_KEY_PASSWORD=your-password
export APPLE_ID=your@email.com
export APPLE_ID_PASSWORD=app-specific-password
```

**Windows:**
```bash
export CSC_LINK=/path/to/certificate.pfx
export CSC_KEY_PASSWORD=your-password
```

See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed instructions.

## 📚 Documentation

Comprehensive documentation is available in the `docs/` directory:

- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)**: System architecture and design patterns
- **[COMPONENTS.md](docs/COMPONENTS.md)**: Component catalog and usage examples
- **[DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md)**: IBM Carbon Design System integration
- **[DEVELOPMENT.md](docs/DEVELOPMENT.md)**: Development workflow and best practices
- **[API_INTEGRATION.md](docs/API_INTEGRATION.md)**: Runtime API and SSE streaming
- **[DEPLOYMENT.md](docs/DEPLOYMENT.md)**: Build and distribution guide

## 🤝 Contributing

### Development Setup

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run tests and linting: `npm run type-check && npm run lint`
5. Commit with descriptive messages
6. Push to your fork
7. Create a Pull Request

### Code Style

- Use TypeScript for all new code
- Follow existing code patterns
- Use functional components with hooks
- Add JSDoc comments for complex functions
- Keep components small and focused
- Use Carbon Design System components when possible

### Commit Messages

Follow conventional commits:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Code style changes (formatting)
- `refactor:` Code refactoring
- `test:` Test additions or changes
- `chore:` Build process or tooling changes

## 🐛 Troubleshooting

### Common Issues

#### Electron won't start

```bash
# Clear node_modules and reinstall
rm -rf node_modules package-lock.json
npm install
```

#### Vite dev server not connecting

```bash
# Check if port 5173 is available
lsof -i :5173

# Kill process if needed
kill -9 <PID>
```

#### TypeScript errors

```bash
# Rebuild TypeScript
npm run build:electron
npm run type-check
```

#### Build fails on macOS

```bash
# Install Xcode Command Line Tools
xcode-select --install
```

#### Build fails on Windows

- Install Visual Studio Build Tools
- Ensure Python 3.x is installed
- Run as Administrator if permission errors occur

#### Hot reload not working

- Check that Vite dev server is running on port 5173
- Restart Electron: `npm run electron:dev`
- Clear browser cache in Electron DevTools

#### Carbon styles not loading

```bash
# Reinstall Carbon packages
npm install @carbon/react @carbon/icons-react
```

### Debug Mode

Enable debug logging:

```bash
# Set environment variable
export DEBUG=rice:*

# Run in development
npm run electron:dev
```

### Getting Help

- Check existing documentation in `docs/`
- Search GitHub issues
- Review console logs in Electron DevTools
- Check network tab for API errors

## 📄 License

MIT License - see LICENSE file for details

## 🙏 Acknowledgments

- **IBM Carbon Design System**: For the excellent design system
- **Electron**: For the cross-platform framework
- **React**: For the UI library
- **Vite**: For the fast build tool

---

**Made with ❤️ by the RICE Team**

For more information, visit the [main RICE repository](../../README.md).