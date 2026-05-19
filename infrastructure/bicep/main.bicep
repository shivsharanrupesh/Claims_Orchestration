// infrastructure/bicep/main.bicep
// ─────────────────────────────────
// Deploys all Azure resources for Option B Balanced Architecture.
//
// WHAT THIS CREATES:
//   - Azure OpenAI (GPT-4o + GPT-4o-mini + text-embedding-3-small deployments)
//   - Azure Cosmos DB (NoSQL, Canada Central, 400 RU autoscale)
//   - Azure Blob Storage (LRS, Canada Central)
//   - Azure Cache for Redis (C1 Standard, SSL enabled)
//   - Azure Container Apps Environment (shared for all container apps)
//   - Azure ML Workspace (for MLflow experiment tracking)
//   - Log Analytics Workspace (for Container Apps telemetry)
//
// USAGE:
//   az group create --name rg-claims-option-b --location canadacentral
//   az deployment group create \
//     --resource-group rg-claims-option-b \
//     --template-file infrastructure/bicep/main.bicep \
//     --parameters @infrastructure/bicep/parameters.dev.json
//
// NOTE: Container Apps are deployed separately by the CI/CD pipeline.
//       This template provisions the infrastructure they depend on.

targetScope = 'resourceGroup'

@description('Location for all resources. Use canadacentral for PIPEDA compliance.')
param location string = 'canadacentral'

@description('Short environment name: dev | staging | prod')
param environment string = 'dev'

@description('Base name for all resources. Resources will be named {baseName}-{service}-{env}.')
param baseName string = 'claims-orchestrator'

@description('Azure OpenAI capacity in thousands of tokens per minute.')
param openAICapacity int = 30

var suffix = '${baseName}-${environment}'
var tags = {
  project: 'claims-orchestrator-option-b'
  environment: environment
  architecture: 'option-b-balanced'
}


// ══════════════════════════════════════════════════════════════
// LOG ANALYTICS (required by Container Apps)
// ══════════════════════════════════════════════════════════════

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'log-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}


// ══════════════════════════════════════════════════════════════
// AZURE OPENAI
// The only Azure AI service we use in Option B.
// Canada Central region for PIPEDA data residency.
// ══════════════════════════════════════════════════════════════

resource openAI 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: 'aoai-${suffix}'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    publicNetworkAccess: 'Enabled'  // switch to Disabled + private endpoint in production
    customSubDomainName: 'aoai-${suffix}'
  }
}

// GPT-4o for reasoning-heavy agents (Triage, Fraud, Manager)
resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAI
  name: 'gpt-4o'
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-08-06'
    }
  }
  sku: { name: 'Standard', capacity: openAICapacity }
}

// GPT-4o-mini for high-volume agents (Document, Enrichment, Routing)
resource gpt4oMiniDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAI
  name: 'gpt-4o-mini'
  dependsOn: [gpt4oDeployment]
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o-mini'
      version: '2024-07-18'
    }
  }
  sku: { name: 'Standard', capacity: openAICapacity }
}

// text-embedding-3-small for ChromaDB RAG embeddings
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAI
  name: 'text-embedding-3-small'
  dependsOn: [gpt4oMiniDeployment]
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: '1'
    }
  }
  sku: { name: 'Standard', capacity: openAICapacity }
}


// ══════════════════════════════════════════════════════════════
// AZURE COSMOS DB
// Stores ClaimState JSON documents.
// Partitioned by /claim_id for O(1) per-claim lookups.
// ══════════════════════════════════════════════════════════════

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2023-04-15' = {
  name: 'cosmos-${suffix}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableAutomaticFailover: false
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'  // good default for claims processing
    }
    locations: [
      { locationName: location, failoverPriority: 0, isZoneRedundant: false }
    ]
    capabilities: [{ name: 'EnableServerless' }]  // serverless for dev; switch to provisioned for prod
  }
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2023-04-15' = {
  parent: cosmosAccount
  name: 'claims-db'
  properties: {
    resource: { id: 'claims-db' }
  }
}

resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2023-04-15' = {
  parent: cosmosDatabase
  name: 'claim-state'
  properties: {
    resource: {
      id: 'claim-state'
      partitionKey: { paths: ['/claim_id'], kind: 'Hash' }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [
          { path: '/stage/?'     }
          { path: '/claim_id/?'  }
          { path: '/updated_at/?' }
        ]
        excludedPaths: [{ path: '/audit/*' }]  // don't index audit array — large
      }
      defaultTtl: -1  // no TTL — keep all claims indefinitely
    }
  }
}


// ══════════════════════════════════════════════════════════════
// AZURE BLOB STORAGE
// Stores claim photos, PDFs, and voice transcripts.
// ══════════════════════════════════════════════════════════════

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: replace('st${suffix}', '-', '')  // storage account names: no hyphens, max 24 chars
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }  // use GRS in production
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false  // all access via SAS URLs or managed identity
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource claimDocumentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'claim-documents'
  properties: {
    publicAccess: 'None'
  }
}


// ══════════════════════════════════════════════════════════════
// AZURE CACHE FOR REDIS
// Managed Redis — message queue between workers.
// C1 Standard = 1GB, 99.9% SLA, SSL, no downtime patching.
// ══════════════════════════════════════════════════════════════

resource redisCache 'Microsoft.Cache/redis@2023-04-01' = {
  name: 'redis-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'Standard'
      family: 'C'
      capacity: 1  // 1GB — sufficient for claim queues
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    redisConfiguration: {
      maxmemory-policy: 'noeviction'  // never drop queue messages
    }
  }
}


// ══════════════════════════════════════════════════════════════
// CONTAINER APPS ENVIRONMENT
// Shared runtime for the API, workers, and MCP servers.
// ChromaDB also runs here as a Container App.
// ══════════════════════════════════════════════════════════════

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'cae-${suffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}


// ══════════════════════════════════════════════════════════════
// AZURE ML WORKSPACE
// Hosts the MLflow tracking server for experiment logging.
// ══════════════════════════════════════════════════════════════

resource mlWorkspace 'Microsoft.MachineLearningServices/workspaces@2023-04-01' = {
  name: 'mlw-${suffix}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    storageAccount: storageAccount.id
    keyVault: keyVault.id
    friendlyName: 'Claims Orchestrator ML Workspace'
  }
}


// ══════════════════════════════════════════════════════════════
// KEY VAULT (used by Azure ML and for secret rotation)
// ══════════════════════════════════════════════════════════════

resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: 'kv-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enabledForTemplateDeployment: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    accessPolicies: []
  }
}


// ══════════════════════════════════════════════════════════════
// OUTPUTS — used by CI/CD to set Container App environment vars
// ══════════════════════════════════════════════════════════════

output openAIEndpoint string          = openAI.properties.endpoint
output cosmosEndpoint string          = cosmosAccount.properties.documentEndpoint
output storageAccountName string      = storageAccount.name
output redisHostName string           = redisCache.properties.hostName
output containerAppsEnvName string    = containerAppsEnv.name
output mlWorkspaceName string         = mlWorkspace.name
output keyVaultUri string             = keyVault.properties.vaultUri
output resourceGroupName string       = resourceGroup().name
