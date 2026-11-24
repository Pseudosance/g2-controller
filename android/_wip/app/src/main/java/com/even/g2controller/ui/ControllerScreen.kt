package com.even.g2controller.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Bluetooth
import androidx.compose.material.icons.filled.BluetoothConnected
import androidx.compose.material.icons.filled.BluetoothDisabled
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.TouchApp
import androidx.compose.runtime.Composable
import androidx.compose.runtime.OptIn
import androidx.compose.runtime.getValue
import androidx.compose.runtime.livedata.observeAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextAlign
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ControllerScreen(viewModel: ControllerViewModel = viewModel()) {
    val uiState by viewModel.uiState.observeAsState()
    val currentUiState = uiState
    
    Column(
        modifier = Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        TopAppBar(
            title = { Text("G2 Controller") },
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = MaterialTheme.colorScheme.surface
            )
        )
        
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            when (currentUiState) {
                is ControllerViewModel.UiState.Disconnected -> DisconnectedScreen(onScanClick = { viewModel.startScan() })
                is ControllerViewModel.UiState.Connecting -> ConnectingScreen(deviceName = currentUiState.deviceName)
                is ControllerViewModel.UiState.Connected -> ControllerButtons(viewModel = viewModel)
                is ControllerViewModel.UiState.Error -> ErrorScreen(message = currentUiState.message, onRetry = { viewModel.startScan() })
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DisconnectedScreen(onScanClick: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Icon(Icons.Filled.BluetoothDisabled, contentDescription = "Disconnected", tint = MaterialTheme.colorScheme.onSurfaceVariant, size = 96.dp)
        androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(16.dp))
        Text("G2 Not Connected", fontSize = 24.sp, fontWeight = FontWeight.Bold)
        androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(8.dp))
        Text("Put your G2 glasses in pairing mode and tap Scan", textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onSurfaceVariant)
        androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(24.dp))
        Button(onClick = onScanClick) {
            Text("Scan for G2", fontSize = 18.sp)
        }
    }
}

@Composable
fun ConnectingScreen(deviceName: String) {
    Column(
        modifier = Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        CircularProgressIndicator()
        androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(16.dp))
        Text("Connecting to $deviceName...", fontSize = 18.sp)
    }
}

@Composable
fun ErrorScreen(message: String, onRetry: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Icon(Icons.Filled.BluetoothDisabled, contentDescription = "Error", tint = MaterialTheme.colorScheme.error, size = 96.dp)
        androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(16.dp))
        Text("Connection Error", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.error)
        androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(8.dp))
        Text(message, textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onSurfaceVariant)
        androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(24.dp))
        Button(onClick = onRetry) {
            Text("Retry")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ControllerButtons(viewModel: ControllerViewModel) {
    Column(
        modifier = Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.Bottom,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
        ) {
            androidx.compose.foundation.layout.Row(
                modifier = Modifier.padding(16.dp),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("G2 Connected", fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface)
                Text("Ready", color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f))
            }
        }
        
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                LargeIconButton(
                    icon = Icons.Filled.ArrowUpward,
                    contentDescription = "Scroll Up",
                    onClick = { viewModel.scrollUp() },
                    label = "Scroll ↑",
                    modifier = Modifier.width(0.dp).weight(1f)
                )
                LargeIconButton(
                    icon = Icons.Filled.ArrowDownward,
                    contentDescription = "Scroll Down",
                    onClick = { viewModel.scrollDown() },
                    label = "Scroll ↓",
                    modifier = Modifier.width(0.dp).weight(1f)
                )
            }
            
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                LargeIconButton(
                    icon = Icons.Filled.TouchApp,
                    contentDescription = "Tap",
                    onClick = { viewModel.tap() },
                    label = "Tap",
                    modifier = Modifier.width(0.dp).weight(1f)
                )
                LargeIconButton(
                    icon = Icons.Filled.TouchApp,
                    contentDescription = "Long Tap",
                    onClick = { viewModel.longTap() },
                    label = "Long Tap",
                    modifier = Modifier.width(0.dp).weight(1f)
                )
                LargeIconButton(
                    icon = Icons.Filled.TouchApp,
                    contentDescription = "Double Tap",
                    onClick = { viewModel.doubleTap() },
                    label = "2× Tap",
                    modifier = Modifier.width(0.dp).weight(1f)
                )
            }
            
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                LargeIconButton(
                    icon = Icons.Filled.ArrowBack,
                    contentDescription = "Back",
                    onClick = { viewModel.navBack() },
                    label = "Back",
                    modifier = Modifier.width(0.dp).weight(1f)
                )
                LargeIconButton(
                    icon = Icons.Filled.Home,
                    contentDescription = "Home",
                    onClick = { viewModel.navHome() },
                    label = "Home",
                    modifier = Modifier.width(0.dp).weight(1f)
                )
            }
        }
        
        androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(24.dp))
    }
}

@Composable
fun LargeIconButton(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    contentDescription: String,
    onClick: () -> Unit,
    label: String,
    modifier: Modifier = Modifier
) {
    val colors = androidx.compose.material3.ButtonDefaults.buttonColors(
        containerColor = MaterialTheme.colorScheme.surfaceVariant,
        contentColor = MaterialTheme.colorScheme.onSurface
    )
    Button(
        onClick = onClick,
        modifier = modifier
            .fillMaxWidth()
            .height(80.dp),
        colors = colors,
        shape = RoundedCornerShape(20.dp)
    ) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Icon(icon, contentDescription = contentDescription, size = 32.dp)
            androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(top = 4.dp))
            Text(label, fontSize = 14.sp, fontWeight = FontWeight.Medium, maxLines = 1)
        }
    }
}
