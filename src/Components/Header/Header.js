import React from 'react'
import { Icon } from '@iconify/react';

import './Header.scss'

const Header = () => {
  return (
    <div className='container'>
        <div className='logo'>
            <h1>user’s inventory</h1>
            </div>
            <div className='avatar'>
              <Icon icon="carbon:user-avatar-filled-alt" width={"54px"} height={"54px"} color='white' /> 
                </div>
    </div>
  )
}

export default Header