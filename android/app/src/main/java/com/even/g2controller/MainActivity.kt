package com.even.g2controller

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.lifecycle.viewmodel.compose.viewModel
import com.even.g2controller.ui.ControllerScreen
import com.even.g2controller.ui.ControllerViewModel

class MainActivity : ComponentActivity() {
    private val viewModel: ControllerViewModel by viewModels()
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            ControllerScreen(viewModel = viewModel)
        }
    }
}
