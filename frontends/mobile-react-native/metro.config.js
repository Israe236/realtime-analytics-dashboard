// Metro bundler config: lets the app import framework-free code from ../shared.
const path = require('node:path');
const { getDefaultConfig } = require('expo/metro-config');

const projectRoot = __dirname;
const sharedRoot = path.resolve(projectRoot, '../shared');

const config = getDefaultConfig(projectRoot);

// Metro only sees files inside watchFolders.
config.watchFolders = [...(config.watchFolders ?? []), sharedRoot];

// The shared package has no dependencies of its own; resolve any package from this app.
config.resolver.nodeModulesPaths = [path.resolve(projectRoot, 'node_modules')];

// Map "@shared/<file>" to ../shared/src/<file> (mirrors the tsconfig "paths" entry).
const upstreamResolve = config.resolver.resolveRequest;
config.resolver.resolveRequest = (context, moduleName, platform) => {
  const target = moduleName.startsWith('@shared/')
    ? path.join(sharedRoot, 'src', moduleName.slice('@shared/'.length))
    : moduleName;
  return (upstreamResolve ?? context.resolveRequest)(context, target, platform);
};

module.exports = config;
